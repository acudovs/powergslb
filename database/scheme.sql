CREATE TABLE `domains` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `domain` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `domains_domain_uindex` (`domain`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `names` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `domain_id` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `names_domain_id_name_uindex` (`domain_id`, `name`),
  KEY `names_name_index` (`name`),
  CONSTRAINT `names_domains_id_fk` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `types` (
  `value` int(11) NOT NULL,
  `type` varchar(16) NOT NULL,
  `description` varchar(255) NOT NULL,
  PRIMARY KEY (`value`),
  UNIQUE KEY `types_type_uindex` (`type`),
  UNIQUE KEY `types_description_uindex` (`description`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `names_types` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name_id` int(11) NOT NULL,
  `type_value` int(11) NOT NULL,
  `ttl` int(11) NOT NULL,
  `persistence` int(11) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `names_types_name_id_type_value_uindex` (`name_id`,`type_value`),
  KEY `names_types_types_value_fk` (`type_value`),
  CONSTRAINT `names_types_names_id_fk` FOREIGN KEY (`name_id`) REFERENCES `names` (`id`),
  CONSTRAINT `names_types_types_value_fk` FOREIGN KEY (`type_value`) REFERENCES `types` (`value`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `contents` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `content` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `contents_content_uindex` (`content`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `monitors` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `monitor` varchar(255) NOT NULL,
  `monitor_json` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `monitors_monitor_uindex` (`monitor`),
  UNIQUE KEY `monitors_monitor_json_uindex` (`monitor_json`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `contents_monitors` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `content_id` int(11) NOT NULL,
  `monitor_id` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `contents_monitors_content_id_monitor_id_uindex` (`content_id`,`monitor_id`),
  KEY `contents_monitors_monitors_id_fk` (`monitor_id`),
  CONSTRAINT `contents_monitors_contents_id_fk` FOREIGN KEY (`content_id`) REFERENCES `contents` (`id`),
  CONSTRAINT `contents_monitors_monitors_id_fk` FOREIGN KEY (`monitor_id`) REFERENCES `monitors` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE `records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name_type_id` int(11) NOT NULL,
  `content_monitor_id` int(11) NOT NULL,
  `disabled` int(11) NOT NULL DEFAULT '0',
  `fallback` int(11) NOT NULL DEFAULT '0',
  `weight` int(11) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `records_name_type_id_content_monitor_id_uindex` (`name_type_id`,`content_monitor_id`),
  KEY `records_contents_monitors_id_fk` (`content_monitor_id`),
  CONSTRAINT `records_names_types_id_fk` FOREIGN KEY (`name_type_id`) REFERENCES `names_types` (`id`),
  CONSTRAINT `records_contents_monitors_id_fk` FOREIGN KEY (`content_monitor_id`) REFERENCES `contents_monitors` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
