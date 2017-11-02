CREATE TABLE `contents` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `content` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `contents_content_uindex` (`content`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `contents_monitors` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `content_id` int(11) NOT NULL,
  `monitor_id` int(11) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `contents_monitors_content_id_monitor_id_uindex` (`content_id`,`monitor_id`),
  KEY `contents_monitors_monitors_id_fk` (`monitor_id`),
  CONSTRAINT `contents_monitors_contents_id_fk` FOREIGN KEY (`content_id`) REFERENCES `contents` (`id`),
  CONSTRAINT `contents_monitors_monitors_id_fk` FOREIGN KEY (`monitor_id`) REFERENCES `monitors` (`id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `domains` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `domain` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `domains_domain_uindex` (`domain`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `lbmethods` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `lbmethod` varchar(50) NOT NULL,
  `lbmethod_description` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `lbmethods_lbmode_uindex` (`lbmethod`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `lboptions` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `lbmethod_id` int(11) NOT NULL,
  `lboption` varchar(255) NOT NULL,
  `lboption_json` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `lboptions_lbmethods_id_fk` (`lbmethod_id`),
  KEY `lboptions_lboption_uindex` (`lboption`),
  KEY `lboptions_lboption_json_uindex` (`lboption_json`),
  CONSTRAINT `lboptions_lbmethod_id_fk` FOREIGN KEY (`lbmethod_id`) REFERENCES `lbmethods` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `monitors` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `monitor` varchar(255) NOT NULL,
  `monitor_json` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `monitors_monitor_uindex` (`monitor`),
  UNIQUE KEY `monitors_monitor_json_uindex` (`monitor_json`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `names` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `domain_id` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `names_domain_id_name_uindex` (`domain_id`,`name`),
  KEY `names_name_index` (`name`),
  CONSTRAINT `names_domains_id_fk` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `names_types` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name_id` int(11) NOT NULL,
  `type_value` int(11) NOT NULL,
  `ttl` int(11) NOT NULL,
  `persistence` int(11) NOT NULL DEFAULT 0,
  `lboption_id` int(11) DEFAULT NULL,
  `lbmethod_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `names_types_name_id_type_value_uindex` (`name_id`,`type_value`),
  KEY `names_types_types_value_fk` (`type_value`),
  KEY `names_types_lboptions_lboption_id_fk` (`lboption_id`),
  KEY `names_types_lbmethods_lbmethod_id_fk` (`lbmethod_id`),
  CONSTRAINT `names_types_lbmethods_lbmethod_id_fk` FOREIGN KEY (`lbmethod_id`) REFERENCES `lbmethods` (`id`),
  CONSTRAINT `names_types_lboptions_lboption_id_fk` FOREIGN KEY (`lboption_id`) REFERENCES `lboptions` (`id`),
  CONSTRAINT `names_types_names_id_fk` FOREIGN KEY (`name_id`) REFERENCES `names` (`id`),
  CONSTRAINT `names_types_types_value_fk` FOREIGN KEY (`type_value`) REFERENCES `types` (`value`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name_type_id` int(11) NOT NULL,
  `content_monitor_id` int(11) NOT NULL,
  `view_id` int(11) NOT NULL,
  `disabled` int(11) NOT NULL DEFAULT 0,
  `fallback` int(11) NOT NULL DEFAULT 0,
  `weight` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `records_name_type_id_content_monitor_id_view_id_uindex` (`name_type_id`,`content_monitor_id`,`view_id`),
  KEY `records_contents_monitors_id_fk` (`content_monitor_id`),
  KEY `records_view_id_fk` (`view_id`),
  CONSTRAINT `records_contents_monitors_id_fk` FOREIGN KEY (`content_monitor_id`) REFERENCES `contents_monitors` (`id`),
  CONSTRAINT `records_names_types_id_fk` FOREIGN KEY (`name_type_id`) REFERENCES `names_types` (`id`),
  CONSTRAINT `records_views_id_fk` FOREIGN KEY (`view_id`) REFERENCES `views` (`id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `types` (
  `value` int(11) NOT NULL,
  `type` varchar(16) NOT NULL,
  `description` varchar(255) NOT NULL,
  PRIMARY KEY (`value`),
  UNIQUE KEY `types_type_uindex` (`type`),
  UNIQUE KEY `types_description_uindex` (`description`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
CREATE TABLE `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `user` varchar(16) NOT NULL,
  `name` varchar(255) NOT NULL,
  `password` char(41) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `users_user_uindex` (`user`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
CREATE TABLE `views` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `view` varchar(255) NOT NULL,
  `rule` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `views_view_uindex` (`view`),
  UNIQUE KEY `views_rule_uindex` (`rule`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8;
