#!/usr/bin/env bash
# tags.sh <image-ref-without-tag> [grep-filter] [hub-name-filter]
# Lists ALL tags (paginated) from ghcr.io / docker.io / any v2 registry. Read-only.
set -u
ref="$1"
filt="${2:-.}"
hubname="${3:-}"
host="${ref%%/*}"
repo="${ref#*/}"
case "$host" in
	docker.io)
		case "$repo" in */*) : ;; *) repo="library/$repo" ;; esac
		url="https://hub.docker.com/v2/repositories/${repo}/tags?page_size=100"
		[ -n "$hubname" ] && url="${url}&name=${hubname}"
		while [ -n "$url" ] && [ "$url" != "null" ]; do
			body=$(curl -s "$url")
			printf '%s\n' "$body" | tr ',' '\n' | grep -oE '"name":"[^"]*"' | sed 's/"name":"//;s/"//'
			url=$(printf '%s' "$body" | grep -oE '"next":"[^"]*"' | head -1 | sed 's/"next":"//;s/"//;s/\\u0026/\&/g')
		done
		;;
	*)
		tok=$(curl -s "https://${host}/token?scope=repository:${repo}:pull&service=${host}" \
			| sed 's/.*"token":"\([^"]*\)".*/\1/')
		last=""
		while :; do
			u="https://${host}/v2/${repo}/tags/list?n=1000"
			[ -n "$last" ] && u="${u}&last=${last}"
			out=$(curl -s -H "Authorization: Bearer $tok" "$u" \
				| tr ',' '\n' | tr -d '"[]{}' | sed 's/tags://;s/name://' | grep -v '^$')
			[ -z "$out" ] && break
			printf '%s\n' "$out"
			n=$(printf '%s\n' "$out" | wc -l)
			[ "$n" -lt 1000 ] && break
			last=$(printf '%s\n' "$out" | tail -1)
		done
		;;
esac | grep -E "$filt" | sort -uV | tail -25
exit 0
