// Prepare the file store's bucket: create it, grant anonymous reads, allow the browser's
// cross-origin uploads. Run by `hooks.after_change` as
// `podman exec lobehub-app /bin/node /config/s3-init.js`.
//
// WHY THIS EXISTS AT ALL
//
// Upstream's compose does this work in a seventh container (`rustfs/rc`, a one-off that
// runs `rc mb`, `rc anonymous set-json` and `rc bucket cors set` and exits). A container
// that exits has no legal home in this repository: every `*.container.j2` must declare
// `Restart=always` and a `HealthCmd=`, which a process that finishes can give neither, and
// a bare `*.service.j2` without a `*.timer.j2` is refused by the static contract. So it is
// an after-change hook, which the role runs once the container it names is healthy.
//
// WHY IT RUNS IN THE APP CONTAINER AND NOT IN THE FILE STORE
//
// The hook needs a runtime, the credentials and an S3 client, and the file store's image
// has only the first two: `rustfs/rustfs` installs ca-certificates, coreutils, curl and
// tzdata, with no `rc`, no shell scripting language and no way to sign a request. The app's
// image is busybox plus a distroless node, and it already carries the S3 credentials --
// `podman exec` starts a process with the container's *configured* environment, which is
// exactly the `Secret=…,type=env` lines in `lobehub-app.container.j2`. So the signing is
// done here, by hand, with node's own crypto and no dependency: nothing in this image's
// bundle is a documented module path, and reaching into `.pnpm` would break on a bump.
//
// WHY IT IS SAFE TO RUN ON EVERY DEPLOY
//
// Creating a bucket that already exists is success: S3 answers 409
// (`BucketAlreadyOwnedByYou`) and RustFS answers plain 200 -- measured in a VM, where a
// second hand-run of this script printed `bucket lobe ready (create 200)` -- so both are
// accepted below. The policy and the CORS rule are whole-document PUTs, so applying them
// twice is applying them once.

const crypto = require('crypto')
const http = require('http')

const endpoint = new URL(
	process.env.S3_INTERNAL_ENDPOINT || 'http://127.0.0.1:9000'
)
const bucket = process.env.S3_BUCKET
const accessKey = process.env.S3_ACCESS_KEY_ID || process.env.S3_ACCESS_KEY
const secretKey = process.env.S3_SECRET_ACCESS_KEY
// The store is not AWS and has no regions; SigV4 has no way to say so, and `us-east-1` is
// what every S3-compatible implementation accepts as the stand-in.
const region = process.env.S3_REGION || 'us-east-1'

if (!bucket || !accessKey || !secretKey) {
	console.error(
		's3-init: S3_BUCKET, the access key and the secret key must all be set'
	)
	process.exit(1)
}

const sha256 = (value) =>
	crypto.createHash('sha256').update(value).digest('hex')
const hmac = (key, value) =>
	crypto.createHmac('sha256', key).update(value).digest()

// One signed request. `query` is already in canonical form: sorted, and each key present
// even when its value is empty, which is how S3 spells a subresource (`?cors=`).
function request({ method, path, query = '', body = '', headers = {} }) {
	const stamp = new Date().toISOString().replace(/[:-]|\.\d{3}/g, '')
	const date = stamp.slice(0, 8)
	const payloadHash = sha256(body)

	// Every key is written lowercase at the call site, which is what SigV4 wants: the
	// canonical headers are the signed ones sorted by name, lowercased, value trimmed.
	const signed = {
		host: endpoint.host,
		'x-amz-content-sha256': payloadHash,
		'x-amz-date': stamp,
		...headers
	}
	const names = Object.keys(signed).sort()
	const canonicalHeaders = names
		.map((n) => `${n}:${String(signed[n]).trim()}\n`)
		.join('')
	const signedHeaders = names.join(';')

	const canonicalRequest = [
		method,
		path,
		query,
		canonicalHeaders,
		signedHeaders,
		payloadHash
	].join('\n')

	const scope = `${date}/${region}/s3/aws4_request`
	const stringToSign = [
		'AWS4-HMAC-SHA256',
		stamp,
		scope,
		sha256(canonicalRequest)
	].join('\n')
	const signingKey = hmac(
		hmac(hmac(hmac(`AWS4${secretKey}`, date), region), 's3'),
		'aws4_request'
	)
	const signature = crypto
		.createHmac('sha256', signingKey)
		.update(stringToSign)
		.digest('hex')

	const authorization =
		`AWS4-HMAC-SHA256 Credential=${accessKey}/${scope}, ` +
		`SignedHeaders=${signedHeaders}, Signature=${signature}`

	return new Promise((resolve, reject) => {
		const req = http.request(
			{
				host: endpoint.hostname,
				port: endpoint.port || 80,
				method,
				path: query ? `${path}?${query}` : path,
				headers: {
					...signed,
					Authorization: authorization,
					'content-length': Buffer.byteLength(body)
				}
			},
			(res) => {
				let text = ''
				res.on('data', (chunk) => (text += chunk))
				res.on('end', () => resolve({ status: res.statusCode, body: text }))
			}
		)
		req.on('error', reject)
		req.end(body)
	})
}

// Anonymous `s3:GetObject` on everything in the bucket -- upstream's own
// `bucket.config.json`, which its one-off container applies the same way. The store is only
// reachable through a VPN-only Traefik route, so "anonymous" means "a browser that already
// reached this name", and it is what lets an <img> tag render an attachment without the page
// signing every URL.
const policy = JSON.stringify({
	Id: '',
	Statement: [
		{
			Action: ['s3:GetObject'],
			Condition: {},
			Effect: 'Allow',
			NotAction: [],
			NotResource: [],
			Principal: { AWS: ['*'] },
			Resource: [`arn:aws:s3:::${bucket}/*`],
			Sid: ''
		}
	],
	Version: '2012-10-17'
})

// The browser PUTs uploads straight at this store with a presigned URL, so the store has to
// answer its preflight. `*` as the origin, exactly as upstream sets it: the app's own name
// is the only page that ever holds such a URL, and the URL's signature is what guards the
// write -- a narrower origin here would buy nothing and break the day the route is renamed.
const cors =
	'<CORSConfiguration><CORSRule>' +
	'<AllowedOrigin>*</AllowedOrigin>' +
	'<AllowedMethod>GET</AllowedMethod><AllowedMethod>PUT</AllowedMethod><AllowedMethod>HEAD</AllowedMethod>' +
	'<AllowedHeader>*</AllowedHeader><ExposeHeader>ETag</ExposeHeader><MaxAgeSeconds>3600</MaxAgeSeconds>' +
	'</CORSRule></CORSConfiguration>'

async function main() {
	const created = await request({ method: 'PUT', path: `/${bucket}` })
	// 409 is S3's `BucketAlreadyOwnedByYou` and 200 is what RustFS answers instead: the
	// second and every later deploy.
	if (created.status !== 200 && created.status !== 409) {
		throw new Error(
			`creating the bucket answered ${created.status}: ${created.body}`
		)
	}

	const policySet = await request({
		method: 'PUT',
		path: `/${bucket}`,
		query: 'policy=',
		body: policy,
		headers: { 'content-type': 'application/json' }
	})
	if (policySet.status !== 200 && policySet.status !== 204) {
		throw new Error(
			`setting the bucket policy answered ${policySet.status}: ${policySet.body}`
		)
	}

	const corsSet = await request({
		method: 'PUT',
		path: `/${bucket}`,
		query: 'cors=',
		body: cors,
		headers: {
			// S3 requires a Content-MD5 on PutBucketCors and rejects the request without it.
			'content-md5': crypto.createHash('md5').update(cors).digest('base64'),
			'content-type': 'application/xml'
		}
	})
	if (corsSet.status !== 200 && corsSet.status !== 204) {
		throw new Error(
			`setting the CORS rule answered ${corsSet.status}: ${corsSet.body}`
		)
	}

	console.log(`s3-init: bucket ${bucket} ready (create ${created.status})`)
}

main().catch((error) => {
	console.error(`s3-init: ${error.message}`)
	process.exit(1)
})
