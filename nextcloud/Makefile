.PHONY: context
context:
	docker context use default

.PHONY: configs
configs:
	docker config create nextcloud_nginx_conf ./config/nginx/etc/nginx/nginx.conf
	docker config create nextcloud_fpm_conf ./config/nextcloud/usr/local/etc/php-fpm.d/www.conf
	docker config create nextcloud_opcache_conf ./config/nextcloud/usr/local/etc/php/conf.d/opcache.ini

.PHONY: secrets
secrets:
	docker secret create nextcloud_database_name nextcloud_database_name.secret
	docker secret create nextcloud_database_password nextcloud_database_password.secret
	docker secret create nextcloud_database_user nextcloud_database_user.secret

.PHONY: deploy
deploy:
	docker stack deploy --detach -c docker-stack.yml nextcloud

.PHONY: rm
rm:
	docker stack rm nextcloud

.PHONY: ps
ps:
	docker stack ps nextcloud
