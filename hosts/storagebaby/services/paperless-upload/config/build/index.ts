// Watches a folder for PDFs and uploads them to Paperless-ngx. Event-driven (fs.watch),
// no polling. An upload that succeeds moves the file to PROCESSED_DIR. Only the auth token is
// external (a Podman secret); everything else is a const below.

import { watch } from 'node:fs'
import { readdir, stat, rename, mkdir } from 'node:fs/promises'
import { join } from 'node:path'

// ── config ──────────────────────────────────────────────────────────────────
// PAPERLESS_URL comes from the unit (service.yml's config.paperless_url); the
// fallback only ever applies to a hand-run container.
const PAPERLESS_URL = Bun.env.PAPERLESS_URL ?? 'https://paperless.example.com'
// The bind root: the scans share itself, which is where the scanner drops PDFs.
// PROCESSED_DIR is a subdirectory of it, so the scan below has to skip directories.
const WATCH_DIR = '/data'
const PROCESSED_DIR = '/data/processed'
const TOKEN_FILE = '/run/secrets/paperless_token'
const STABILITY_MS = 2000 // file size must hold steady this long before upload
const DEBOUNCE_MS = 400 // collapse bursts of fs.watch events into one scan
// ─────────────────────────────────────────────────────────────────────────────

const endpoint = `${PAPERLESS_URL.replace(/\/+$/, '')}/api/documents/post_document/`
const token = (await Bun.file(TOKEN_FILE).text()).trim()
const log = (...a: unknown[]) => console.log(new Date().toISOString(), ...a)
const inflight = new Set<string>()

async function stable(path: string): Promise<boolean> {
	try {
		const s = await stat(path)
		// The watch root is the share itself, so `processed/` -- and any other folder
		// the operator keeps there -- shows up in the scan. Only regular files upload.
		if (!s.isFile() || s.size === 0) return false
		await Bun.sleep(STABILITY_MS)
		return (await stat(path)).size === s.size
	} catch {
		return false // vanished mid-check
	}
}

async function upload(path: string, name: string): Promise<boolean> {
	const form = new FormData()
	form.append('document', Bun.file(path), name)
	try {
		const res = await fetch(endpoint, {
			method: 'POST',
			headers: { Authorization: `Token ${token}` },
			body: form
		})
		if (res.ok) {
			log('uploaded', name)
			return true // 200 = accepted for ingestion -> safe to move
		}
		log('rejected', name, res.status, (await res.text()).slice(0, 200))
		return false
	} catch (e) {
		log('failed (network)', name, String(e))
		return false
	}
}

async function dispose(path: string, name: string) {
	let target = join(PROCESSED_DIR, name)
	try {
		await stat(target)
		target = join(PROCESSED_DIR, `${Date.now()}-${name}`) // collision
	} catch {
		/* free */
	}
	await rename(path, target)
	log('archived', name)
}

async function processFile(name: string) {
	if (!name.toLowerCase().endsWith('.pdf')) return
	const path = join(WATCH_DIR, name)
	if (inflight.has(path)) return
	inflight.add(path)
	try {
		if (await stable(path)) {
			if (await upload(path, name)) await dispose(path, name)
		}
	} finally {
		inflight.delete(path)
	}
}

async function scan() {
	let names: string[]
	try {
		names = await readdir(WATCH_DIR)
	} catch {
		return
	}
	// Re-scanning the whole dir on each trigger means a new arrival also retries
	// anything that failed earlier — self-healing, still no polling timer.
	await Promise.all(names.map(processFile))
}

let timer: ReturnType<typeof setTimeout> | null = null
function trigger() {
	if (timer) clearTimeout(timer)
	timer = setTimeout(scan, DEBOUNCE_MS)
}

// Created up front, not on the first upload: this is also what the unit's
// `HealthCmd=test -d /data/processed` checks, so it exists exactly when the uploader
// has started and could write into the bind. If it is ever removed underneath us the
// health check kills the container and this line puts it back.
await mkdir(PROCESSED_DIR, { recursive: true })
log(`watching ${WATCH_DIR} -> ${endpoint}`)
await scan() // one-shot reconcile of files already present at startup
watch(WATCH_DIR, () => trigger())
