<?php

/**
 * The two log settings this platform declares for Nextcloud.
 *
 * Nextcloud reads `config.php` first and then every `*.config.php` beside it in
 * natural sort order, later files winning -- which is why this one is named to sort
 * after the image's own seven. It therefore overrides whatever is in `config.php`,
 * including a value set from the admin UI: the UI's write lands in `config.php` and
 * this file still wins. Changing either value means changing this file in git, and
 * there is no second place to set them.
 *
 * Mounted read-only, so Nextcloud cannot rewrite it. It only ever writes
 * `config.php`, and the "configuration file is writable" setup check reads that one.
 */

$CONFIG = [
	/**
	 * Nextcloud's own default, and what the installation was not running on: it sat at
	 * 0 (debug) and wrote 19212 entries in six and a half hours -- 4975 distinct
	 * requests, so about four per page view, with one single request accounting for
	 * 620 of them. Over the same week `occ setupchecks` found exactly one real
	 * warning. 2 keeps that one and drops the rest.
	 */
	'loglevel' => 2,

	/**
	 * `errorlog` hands every entry to PHP's `error_log`, which the image points at
	 * /proc/self/fd/2 (its own `php-fpm.d/docker.conf`) -- the container's stderr, so
	 * the journal. Read it with `cradle-svc logs nextcloud`.
	 *
	 * The default, `file`, writes `<datadirectory>/nextcloud.log`, and `datadirectory`
	 * is the `data` volume: class `pool`, a mergerfs union over the WD Reds. Every
	 * entry was an open, flock, append and close on a spinning disk through FUSE,
	 * which is what made the installation feel slow on every click. The split this
	 * service already makes -- installation and database on the SSD, user files on the
	 * pool -- was never wrong; Nextcloud's log just defaults into the pool half of it.
	 *
	 * The cost is the `logreader` app, the admin UI's log viewer: it reads the file and
	 * now has none.
	 */
	'log_type' => 'errorlog'
];
