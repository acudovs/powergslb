CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user` varchar(255) NOT NULL,
  `name` varchar(255) NOT NULL,
  `password` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `users_user_uindex` (`user`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `views` (
  `id` int NOT NULL AUTO_INCREMENT,
  `view` varchar(255) NOT NULL,
  `rule` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `views_view_uindex` (`view`),
  UNIQUE KEY `views_rule_uindex` (`rule`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `types` (
  `value` int NOT NULL,
  `type` varchar(16) NOT NULL,
  `description` varchar(255) NOT NULL,
  PRIMARY KEY (`value`),
  UNIQUE KEY `types_type_uindex` (`type`),
  UNIQUE KEY `types_description_uindex` (`description`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `monitors` (
  `id` int NOT NULL AUTO_INCREMENT,
  `monitor` varchar(255) NOT NULL,
  `monitor_json` varchar(1024) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `monitors_monitor_uindex` (`monitor`),
  CONSTRAINT `monitors_monitor_json_check` CHECK (JSON_VALID(`monitor_json`))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `routings` (
  `id` int NOT NULL AUTO_INCREMENT,
  `policy` varchar(255) NOT NULL,
  `policy_json` varchar(1024) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `routings_policy_uindex` (`policy`),
  CONSTRAINT `routings_policy_json_check` CHECK (JSON_VALID(`policy_json`))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `domains` (
  `id` int NOT NULL AUTO_INCREMENT,
  `domain` varchar(255) NOT NULL,
  `description` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `domains_domain_uindex` (`domain`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ttl is an RRset property and lives here, so per-record divergence is unrepresentable.
-- routing_id names the answer-selection strategy for the whole rrset.
-- name is relative to the zone: '@' for the apex, else the labels left of the zone.
CREATE TABLE `rrsets` (
  `id` int NOT NULL AUTO_INCREMENT,
  `domain_id` int NOT NULL,
  `name` varchar(255) NOT NULL,
  `type_value` int NOT NULL,
  `ttl` int unsigned NOT NULL,
  `routing_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `rrsets_domain_id_name_type_value_uindex` (`domain_id`, `name`, `type_value`),
  KEY `rrsets_type_value_index` (`type_value`),
  KEY `rrsets_routing_id_index` (`routing_id`),
  CONSTRAINT `rrsets_domains_id_fk` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`),
  CONSTRAINT `rrsets_types_value_fk` FOREIGN KEY (`type_value`) REFERENCES `types` (`value`),
  CONSTRAINT `rrsets_routings_id_fk` FOREIGN KEY (`routing_id`) REFERENCES `routings` (`id`),
  CONSTRAINT `rrsets_soa_apex_check` CHECK (`type_value` <> 6 OR `name` = '@'),
  CONSTRAINT `rrsets_ttl_check` CHECK (`ttl` <= 2147483647)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- A record is one answer inside a rrset. The rrset FK restricts: a populated rrset cannot be deleted;
-- records are deleted first, and the GC triggers then collect the empty rrset.
CREATE TABLE `records` (
  `id` int NOT NULL AUTO_INCREMENT,
  `rrset_id` int NOT NULL,
  `content` varchar(255) NOT NULL,
  `monitor_id` int NOT NULL,
  `view_id` int NOT NULL,
  `disabled` tinyint(1) NOT NULL DEFAULT 0,
  `weight` int unsigned NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `records_rrset_id_view_id_content_uindex` (`rrset_id`, `view_id`, `content`),
  KEY `records_monitor_id_index` (`monitor_id`),
  KEY `records_view_id_index` (`view_id`),
  CONSTRAINT `records_rrsets_id_fk` FOREIGN KEY (`rrset_id`) REFERENCES `rrsets` (`id`),
  CONSTRAINT `records_monitors_id_fk` FOREIGN KEY (`monitor_id`) REFERENCES `monitors` (`id`),
  CONSTRAINT `records_views_id_fk` FOREIGN KEY (`view_id`) REFERENCES `views` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Append-only trail of admin writes: one row per record.
-- user is the login string, not an FK, so the trail survives user deletion or rename.
CREATE TABLE `audit` (
  `id` int NOT NULL AUTO_INCREMENT,
  `logged` datetime NOT NULL DEFAULT current_timestamp(),
  `user` varchar(255) NOT NULL,
  `client_ip` varchar(45) NOT NULL,
  `action` varchar(16) NOT NULL,
  `data` varchar(32) NOT NULL,
  `record_id` int NOT NULL,
  `record_before` text DEFAULT NULL,
  `record_after` text DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `audit_logged_index` (`logged`),
  CONSTRAINT `audit_record_state_check` CHECK (`record_before` IS NOT NULL OR `record_after` IS NOT NULL),
  CONSTRAINT `audit_record_before_check` CHECK (`record_before` IS NULL OR JSON_VALID(`record_before`)),
  CONSTRAINT `audit_record_after_check` CHECK (`record_after` IS NULL OR JSON_VALID(`record_after`))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER //

CREATE PROCEDURE `rrset_guard`(IN `p_rrset_id` INT, IN `p_domain_id` INT,
                               IN `p_name` VARCHAR(255), IN `p_type_value` INT)
BEGIN
  IF `p_type_value` = 5 AND EXISTS (SELECT 1 FROM `rrsets`
      WHERE `domain_id` = `p_domain_id` AND `name` = `p_name` AND `id` <> COALESCE(`p_rrset_id`, 0)) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'CNAME rrset conflicts with other rrsets at this name';
  END IF;

  IF `p_type_value` <> 5 AND EXISTS (SELECT 1 FROM `rrsets`
      WHERE `domain_id` = `p_domain_id` AND `name` = `p_name` AND `type_value` = 5
        AND `id` <> COALESCE(`p_rrset_id`, 0)) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'name already has a CNAME rrset';
  END IF;

  IF `p_type_value` = 6 AND `p_rrset_id` IS NOT NULL
     AND (SELECT COUNT(*) FROM `records` WHERE `rrset_id` = `p_rrset_id`) > 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SOA rrset allows exactly one record';
  END IF;
END//

CREATE TRIGGER `rrsets_before_insert` BEFORE INSERT ON `rrsets` FOR EACH ROW
  CALL `rrset_guard`(NULL, NEW.`domain_id`, NEW.`name`, NEW.`type_value`)//

CREATE TRIGGER `rrsets_before_update` BEFORE UPDATE ON `rrsets` FOR EACH ROW
  CALL `rrset_guard`(OLD.`id`, NEW.`domain_id`, NEW.`name`, NEW.`type_value`)//

CREATE TRIGGER `records_before_insert` BEFORE INSERT ON `records` FOR EACH ROW
BEGIN
  DECLARE `v_type` INT;
  SELECT `type_value` INTO `v_type` FROM `rrsets` WHERE `id` = NEW.`rrset_id`;

  IF `v_type` = 6 AND EXISTS (SELECT 1 FROM `records` WHERE `rrset_id` = NEW.`rrset_id`) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SOA rrset allows exactly one record';
  END IF;

  IF `v_type` = 5 AND EXISTS (SELECT 1 FROM `records`
      WHERE `rrset_id` = NEW.`rrset_id` AND `view_id` = NEW.`view_id`) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'CNAME rrset allows one record per view';
  END IF;
END//

CREATE TRIGGER `records_before_update` BEFORE UPDATE ON `records` FOR EACH ROW
BEGIN
  DECLARE `v_type` INT;
  SELECT `type_value` INTO `v_type` FROM `rrsets` WHERE `id` = NEW.`rrset_id`;

  IF `v_type` = 6 AND NEW.`rrset_id` <> OLD.`rrset_id`
     AND EXISTS (SELECT 1 FROM `records` WHERE `rrset_id` = NEW.`rrset_id`) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SOA rrset allows exactly one record';
  END IF;

  IF `v_type` = 5
     AND (NEW.`rrset_id` <> OLD.`rrset_id` OR NEW.`view_id` <> OLD.`view_id`)
     AND EXISTS (SELECT 1 FROM `records`
                 WHERE `rrset_id` = NEW.`rrset_id` AND `view_id` = NEW.`view_id`
                   AND `id` <> NEW.`id`) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'CNAME rrset allows one record per view';
  END IF;
END//

CREATE TRIGGER `records_after_delete` AFTER DELETE ON `records` FOR EACH ROW
BEGIN
  IF NOT EXISTS (SELECT 1 FROM `records` WHERE `rrset_id` = OLD.`rrset_id`) THEN
    DELETE FROM `rrsets` WHERE `id` = OLD.`rrset_id`;
  END IF;
END//

CREATE TRIGGER `records_after_update` AFTER UPDATE ON `records` FOR EACH ROW
BEGIN
  IF NEW.`rrset_id` <> OLD.`rrset_id`
     AND NOT EXISTS (SELECT 1 FROM `records` WHERE `rrset_id` = OLD.`rrset_id`) THEN
    DELETE FROM `rrsets` WHERE `id` = OLD.`rrset_id`;
  END IF;
END//

DELIMITER ;
