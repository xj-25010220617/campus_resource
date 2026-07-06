-- =====================================================
-- 校园资源平台 - 数据库字段批注脚本
-- 执行方式: mysql -h host -u user -p dbname < add_field_comments.sql
-- =====================================================

-- ========== college 表 ==========
ALTER TABLE `college` COMMENT '二级学院表(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
ALTER TABLE `college` CHANGE COLUMN `name` `name` varchar(64) DEFAULT NULL COMMENT '学院名称(id:1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
ALTER TABLE `college` CHANGE COLUMN `code` `code` varchar(16) DEFAULT NULL COMMENT '学院代码(CS/ISE/IBM/DAD/FL/HMT/HVC/IEC/BED/MAR/IEE/CED/VET)';

-- ========== discipline 表 ==========
ALTER TABLE `discipline` COMMENT '学科方向表(工学/管理学/艺术学/文学/医学5大门类)';
ALTER TABLE `discipline` CHANGE COLUMN `name` `name` varchar(64) DEFAULT NULL COMMENT '学科方向名称(如：软件工程/计算机科学与技术)';
ALTER TABLE `discipline` CHANGE COLUMN `category` `category` varchar(16) DEFAULT NULL COMMENT '学科门类(工学/管理学/艺术学/文学/医学)';

-- ========== category 表 ==========
ALTER TABLE `category` COMMENT '资源类型表(1=教学课件,2=试题库,3=实验指导,4=规章制度,5=培养方案,6=论文摘要,7=参考资料,8=官网公告,9=新闻动态)';
ALTER TABLE `category` CHANGE COLUMN `name` `name` varchar(64) DEFAULT NULL COMMENT '类型名称(id:1=教学课件,2=试题库,3=实验指导,4=规章制度,5=培养方案,6=论文摘要,7=参考资料,8=官网公告,9=新闻动态)';

-- ========== department 表 ==========
ALTER TABLE `department` COMMENT '部门表(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育与国际教育)';

-- ========== user 表 (需要临时禁用外键检查) ==========
SET FOREIGN_KEY_CHECKS=0;
ALTER TABLE `user` CHANGE COLUMN `college_id` `college_id` bigint DEFAULT NULL COMMENT '所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
ALTER TABLE `user` CHANGE COLUMN `department_id` `department_id` bigint DEFAULT NULL COMMENT '所属部门(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育)';
SET FOREIGN_KEY_CHECKS=1;

-- ========== resource 表 ==========
SET FOREIGN_KEY_CHECKS=0;
ALTER TABLE `resource` COMMENT '资源表(外键说明: college_id=学院, department_id=部门, category_id=资源类型, discipline_id=学科方向)';
ALTER TABLE `resource` CHANGE COLUMN `college_id` `college_id` bigint DEFAULT NULL COMMENT '所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
ALTER TABLE `resource` CHANGE COLUMN `department_id` `department_id` bigint DEFAULT NULL COMMENT '所属部门(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育)';
ALTER TABLE `resource` CHANGE COLUMN `category_id` `category_id` bigint NOT NULL COMMENT '资源类型(1=教学课件,2=试题库,3=实验指导,4=规章制度,5=培养方案,6=论文摘要,7=参考资料,8=官网公告,9=新闻动态)';
ALTER TABLE `resource` CHANGE COLUMN `discipline_id` `discipline_id` bigint DEFAULT NULL COMMENT '学科方向(1=软件工程,2=计算机,3=网络,4=物联网,5=网安,6=集成电路,7=大数据中外,8=大数据管理,9=机器人,10=信息工程,11=智能科学,12=数据科学,13=AI,14=信管,15=供应链,16=人力,17=电商,18=财务,19=数媒技术,20=数媒艺术,21=艺术与科技,22=VR,23=工业设计,24=视觉传达,25=动画,26=英语,27=商务英语,28=日语,29=公共外语,30=留学生,31=智能医学,32=医学信息,33=医学影像,34=健康服务,35=数字动画,36=软件技术,37=AI技术,38=医养照护)';
SET FOREIGN_KEY_CHECKS=1;

-- ========== upload_log 表 ==========
SET FOREIGN_KEY_CHECKS=0;
ALTER TABLE `upload_log` COMMENT '上传日志表';
ALTER TABLE `upload_log` CHANGE COLUMN `college_id` `college_id` bigint NOT NULL COMMENT '所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
ALTER TABLE `upload_log` CHANGE COLUMN `department_id` `department_id` bigint DEFAULT NULL COMMENT '所属部门(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育)';
SET FOREIGN_KEY_CHECKS=1;

-- ========== knowledge_doc 表 ==========
SET FOREIGN_KEY_CHECKS=0;
ALTER TABLE `knowledge_doc` COMMENT '知识文档表(外键说明: college_id=学院, department_id=部门, category_id=资源类型, discipline_id=学科方向)';
ALTER TABLE `knowledge_doc` CHANGE COLUMN `college_id` `college_id` bigint DEFAULT NULL COMMENT '所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
ALTER TABLE `knowledge_doc` CHANGE COLUMN `department_id` `department_id` bigint DEFAULT NULL COMMENT '所属部门(1=校级领导,2=党政,3=教学管理,4=学生工作,5=科研服务,6=后勤,7=继续教育)';
ALTER TABLE `knowledge_doc` CHANGE COLUMN `category_id` `category_id` bigint DEFAULT NULL COMMENT '资源类型(1=教学课件,2=试题库,3=实验指导,4=规章制度,5=培养方案,6=论文摘要,7=参考资料,8=官网公告,9=新闻动态)';
ALTER TABLE `knowledge_doc` CHANGE COLUMN `discipline_id` `discipline_id` bigint DEFAULT NULL COMMENT '学科方向(1=软件工程,2=计算机,3=网络,4=物联网,5=网安,6=集成电路,7=大数据中外,8=大数据管理,9=机器人,10=信息工程,11=智能科学,12=数据科学,13=AI,14=信管,15=供应链,16=人力,17=电商,18=财务,19=数媒技术,20=数媒艺术,21=艺术与科技,22=VR,23=工业设计,24=视觉传达,25=动画,26=英语,27=商务英语,28=日语,29=公共外语,30=留学生,31=智能医学,32=医学信息,33=医学影像,34=健康服务,35=数字动画,36=软件技术,37=AI技术,38=医养照护)';
SET FOREIGN_KEY_CHECKS=1;

-- ========== faq_item 表 ==========
SET FOREIGN_KEY_CHECKS=0;
ALTER TABLE `faq_item` COMMENT 'FAQ条目表(NULL=全局; 1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
ALTER TABLE `faq_item` CHANGE COLUMN `college_id` `college_id` bigint DEFAULT NULL COMMENT '所属学院(NULL=全局; 1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
SET FOREIGN_KEY_CHECKS=1;

-- ========== feedback_question 表 ==========
SET FOREIGN_KEY_CHECKS=0;
ALTER TABLE `feedback_question` COMMENT '待人工回答问题表';
ALTER TABLE `feedback_question` CHANGE COLUMN `college_id` `college_id` bigint DEFAULT NULL COMMENT '所属学院(1=计算机,2=智能科学,3=信商管理,4=数字艺术,5=外国语,6=健康医疗,7=高职,8=国际,9=基础,10=马院,11=创业,12=继续教育,13=退役军人)';
SET FOREIGN_KEY_CHECKS=1;
