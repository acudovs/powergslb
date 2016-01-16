-- MySQL dump 10.14  Distrib 5.5.44-MariaDB, for Linux (x86_64)
--
-- Host: localhost    Database: powergslb
-- ------------------------------------------------------
-- Server version       5.5.44-MariaDB

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `checks`
--

DROP TABLE IF EXISTS `checks`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `checks` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `check` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `checks_name_uindex` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `checks`
--

LOCK TABLES `checks` WRITE;
/*!40000 ALTER TABLE `checks` DISABLE KEYS */;
INSERT INTO `checks` VALUES (1,'ICMP ping','{\"type\": \"icmp\", \"ip\": \"%(content)s\", \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(2,'TCP connect','{\"type\": \"tcp\", \"ip\": \"%(content)s\", \"port\": 8080, \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(3,'HTTP request','{\"type\": \"http\", \"url\": \"http://%(content)s:8080/status\", \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(4,'Command status','{\"type\": \"command\", \"path\": \"/etc/powergslb/powergslb-check\", \"argument\": \"%(content)s\", \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}');
/*!40000 ALTER TABLE `checks` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `domains`
--

DROP TABLE IF EXISTS `domains`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `domains` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `domains_name_uindex` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `domains`
--

LOCK TABLES `domains` WRITE;
/*!40000 ALTER TABLE `domains` DISABLE KEYS */;
INSERT INTO `domains` VALUES (1,'example.com');
/*!40000 ALTER TABLE `domains` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `records`
--

DROP TABLE IF EXISTS `records`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `records` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `domain_id` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  `type` varchar(10) NOT NULL,
  `content` varchar(255) NOT NULL,
  `ttl` int(11) NOT NULL,
  `priority` int(11) NOT NULL DEFAULT '0',
  `disabled` int(11) NOT NULL DEFAULT '0',
  `check_id` int(11) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `records_name_type_disabled_index` (`name`,`type`,`disabled`),
  KEY `records_name_disabled_index` (`name`,`disabled`),
  KEY `records_checks_id_fk` (`check_id`),
  KEY `records_domains_id_fk` (`domain_id`),
  CONSTRAINT `records_checks_id_fk` FOREIGN KEY (`check_id`) REFERENCES `checks` (`id`) ON DELETE SET NULL,
  CONSTRAINT `records_domains_id_fk` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=20 DEFAULT CHARSET=utf8;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `records`
--

LOCK TABLES `records` WRITE;
/*!40000 ALTER TABLE `records` DISABLE KEYS */;
INSERT INTO `records` VALUES (1,1,'example.com','SOA','ns1.example.com. hostmaster.example.com. 2016010101 21600 3600 1209600 300',86400,0,0,NULL),(2,1,'example.com','NS','ns1.example.com',3600,0,0,NULL),(3,1,'example.com','NS','ns2.example.com',3600,0,0,NULL),(4,1,'example.com','NS','ns3.example.com',3600,0,0,NULL),(5,1,'example.com','NS','ns4.example.com',3600,0,0,NULL),(6,1,'ns1.example.com','A','192.168.200.201',300,0,0,NULL),(7,1,'ns2.example.com','A','192.168.200.202',300,0,0,NULL),(8,1,'ns3.example.com','A','192.168.200.203',300,0,0,NULL),(9,1,'ns4.example.com','A','192.168.200.204',300,0,0,NULL),(10,1,'example.com','A','192.168.200.201',300,0,0,NULL),(11,1,'example.com','A','192.168.200.202',300,0,0,NULL),(12,1,'example.com','A','192.168.200.203',300,0,0,NULL),(13,1,'www.example.com','A','192.168.200.201',300,0,0,NULL),(14,1,'www.example.com','A','192.168.200.202',300,0,0,NULL),(15,1,'www.example.com','A','192.168.200.203',300,0,0,NULL),(16,1,'example.com','MX','mail1.example.com',300,10,0,NULL),(17,1,'example.com','MX','mail2.example.com',300,20,0,NULL),(18,1,'mail1.example.com','A','192.168.200.210',300,0,0,NULL),(19,1,'mail2.example.com','A','192.168.200.220',300,0,0,NULL);
/*!40000 ALTER TABLE `records` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2016-01-15 13:52:12