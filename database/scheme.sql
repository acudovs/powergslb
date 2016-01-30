CREATE TABLE `domains` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `domain` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `domains_domain_uindex` (`domain`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `type` varchar(10) NOT NULL,
  `ttl` int(11) NOT NULL,
  `disabled` int(11) NOT NULL DEFAULT '0',
  `persistence` int(11) NOT NULL DEFAULT '0',
  `domain_id` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `records_name_type_uindex` (`name`,`type`),
  KEY `records_domains_id_fk` (`domain_id`),
  CONSTRAINT `records_domains_id_fk` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `monitors` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `monitor` varchar(255) NOT NULL,
  `description` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `monitors_monitor_uindex` (`monitor`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `contents` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `content` varchar(255) NOT NULL,
  `fallback` int(11) NOT NULL DEFAULT '0',
  `weight` int(11) NOT NULL DEFAULT '0',
  `monitor_id` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `contents_content_fallback_weight_monitor_id_uindex` (`content`,`fallback`,`weight`,`monitor_id`),
  KEY `contents_monitors_id_fk` (`monitor_id`),
  CONSTRAINT `contents_monitors_id_fk` FOREIGN KEY (`monitor_id`) REFERENCES `monitors` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `records_contents` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `record_id` int(11) NOT NULL,
  `content_id` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `records_contents_record_id_content_id_uindex` (`record_id`,`content_id`),
  KEY `records_contents_records_id_fk` (`record_id`),
  KEY `records_contents_contents_id_fk` (`content_id`),
  CONSTRAINT `records_contents_contents_id_fk` FOREIGN KEY (`content_id`) REFERENCES `contents` (`id`) ON DELETE CASCADE,
  CONSTRAINT `records_contents_records_id_fk` FOREIGN KEY (`record_id`) REFERENCES `records` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
