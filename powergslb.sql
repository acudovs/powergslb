-- MySQL dump 10.15  Distrib 10.0.21-MariaDB, for Linux (x86_64)
--
-- Host: localhost    Database: powergslb
-- ------------------------------------------------------
-- Server version	10.0.21-MariaDB

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
  `disabled` int(11) NOT NULL DEFAULT '0',
  `fallback` int(11) NOT NULL DEFAULT '0',
  `persistence` int(11) NOT NULL DEFAULT '0',
  `weight` int(11) NOT NULL DEFAULT '0',
  `monitor` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `records_domains_id_fk` (`domain_id`),
  KEY `records_name_type_index` (`name`,`type`),
  CONSTRAINT `records_domains_id_fk` FOREIGN KEY (`domain_id`) REFERENCES `domains` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=42 DEFAULT CHARSET=utf8;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `records`
--

LOCK TABLES `records` WRITE;
/*!40000 ALTER TABLE `records` DISABLE KEYS */;
INSERT INTO `records` VALUES (1,1,'example.com','SOA','ns1.example.com. hostmaster.example.com. 2016010101 21600 3600 1209600 300',86400,0,0,0,0,NULL),(2,1,'example.com','NS','ns1.example.com',3600,0,0,0,0,NULL),(3,1,'example.com','NS','ns2.example.com',3600,0,0,0,0,NULL),(4,1,'example.com','NS','ns3.example.com',3600,0,0,0,0,NULL),(5,1,'example.com','NS','ns4.example.com',3600,0,0,0,0,NULL),(6,1,'ns1.example.com','A','192.0.2.1',300,0,0,0,0,NULL),(7,1,'ns2.example.com','A','192.0.2.2',300,0,0,0,0,NULL),(8,1,'ns3.example.com','A','192.0.2.3',300,0,0,0,0,NULL),(9,1,'ns4.example.com','A','192.0.2.4',300,0,0,0,0,NULL),(10,1,'ns1.example.com','AAAA','2001:db8::1',300,0,0,0,0,NULL),(11,1,'ns2.example.com','AAAA','2001:db8::2',300,0,0,0,0,NULL),(12,1,'ns3.example.com','AAAA','2001:db8::3',300,0,0,0,0,NULL),(13,1,'ns4.example.com','AAAA','2001:db8::4',300,0,0,0,0,NULL),(14,1,'example.com','A','192.0.2.101',300,0,1,0,100,'{\"type\": \"exec\", \"args\": [\"/etc/powergslb/powergslb-check\", \"%(id)s\", \"%(name)s\", \"%(type)s\", \"%(content)s\", \"%(ttl)s\"], \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(15,1,'example.com','A','192.0.2.102',300,0,1,0,100,'{\"type\": \"icmp\", \"ip\": \"%(content)s\", \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(16,1,'example.com','A','192.0.2.103',300,0,1,0,0,'{\"type\": \"tcp\", \"ip\": \"%(content)s\", \"port\": 80, \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(17,1,'example.com','A','192.0.2.104',300,0,1,0,0,'{\"type\": \"http\", \"url\": \"http://%(content)s/%(name)s/status\", \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(18,1,'example.com','AAAA','2001:db8::101',300,0,0,1,0,NULL),(19,1,'example.com','AAAA','2001:db8::102',300,0,0,1,0,NULL),(20,1,'example.com','AAAA','2001:db8::103',300,0,0,1,0,NULL),(21,1,'example.com','AAAA','2001:db8::104',300,0,0,1,0,NULL),(22,1,'www.example.com','CNAME','example.com',3600,0,0,0,0,NULL),(23,1,'m.example.com','A','192.0.2.201',300,0,0,1,0,NULL),(24,1,'m.example.com','A','192.0.2.202',300,0,0,1,0,NULL),(25,1,'m.example.com','A','192.0.2.203',300,0,0,1,0,NULL),(26,1,'m.example.com','A','192.0.2.204',300,0,0,1,0,NULL),(27,1,'m.example.com','AAAA','2001:db8::201',300,0,0,0,0,'{\"type\": \"exec\", \"args\": [\"/etc/powergslb/powergslb-check\", \"%(id)s\", \"%(name)s\", \"%(type)s\", \"%(content)s\", \"%(ttl)s\"], \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(28,1,'m.example.com','AAAA','2001:db8::202',300,0,0,0,0,'{\"type\": \"icmp\", \"ip\": \"%(content)s\", \"interval\": 3, \"timeout\": 1, \"fall\": 3, \"rise\": 5}'),(29,1,'m.example.com','AAAA','2001:db8::203',300,0,1,0,0,NULL),(30,1,'m.example.com','AAAA','2001:db8::204',300,0,1,0,0,NULL),(31,1,'mobile.example.com','CNAME','m.example.com',3600,0,0,0,0,NULL),(32,1,'example.com','MX','10 mail1.example.com',3600,0,0,0,0,NULL),(33,1,'example.com','MX','20 mail2.example.com',3600,0,0,0,0,NULL),(34,1,'example.com','TXT','v=spf1 ip4:192.0.2.0/24 2001:db8::/32 ~all',3600,0,0,0,0,NULL),(35,1,'mail1.example.com','A','192.0.2.10',300,0,0,0,0,NULL),(36,1,'mail2.example.com','A','192.0.2.20',300,0,0,0,0,NULL),(37,1,'mail1.example.com','AAAA','2001:db8::10',300,0,0,0,0,NULL),(38,1,'mail2.example.com','AAAA','2001:db8::20',300,0,0,0,0,NULL),(39,1,'_sip._tcp.example.com','SRV','10 100 5060 sip.example.com',3600,0,0,0,0,NULL),(40,1,'sip.example.com','A','192.0.2.30',300,0,0,0,0,NULL),(41,1,'sip.example.com','AAAA','2001:db8::30',300,0,0,0,0,NULL);
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

-- Dump completed on 2016-01-20 19:11:03
