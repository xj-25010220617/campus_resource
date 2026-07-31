"""
种子测试文档生成脚本：从成都东软学院官网数据创建多种类型知识文档，用于功能测试和演示。

文档类型覆盖：
  - 学校概况（规章制度类）
  - 院系专业设置（参考资料类）
  - 招生简章2025（官网公告类）
  - 校园生活指南（参考资料类）
  - 奖助学金政策（规章制度类）
  - 图书馆使用指南（参考资料类）
  - 学期安排与校历（官网公告类）
  - 学生社团介绍（新闻动态类）
  - 录取规则与政策（规章制度类）
  - 科研与实验室（新闻动态类）

用法：
    python scripts/seed_test_docs.py                    # 创建文档但不索引（status=uploaded）
    python scripts/seed_test_docs.py --index            # 创建并索引
    python scripts/seed_test_docs.py --force            # 强制重建（删除已有同名文档）
    python scripts/seed_test_docs.py --index --force    # 强制重建并索引
    python scripts/seed_test_docs.py --list             # 仅列出已存在的种子文档
"""
import logging
import os
import sys
import time

# 设置 Django 环境
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# 减少第三方库噪音
logging.getLogger('chromadb').setLevel(logging.WARNING)
logging.getLogger('pymilvus').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)


# =============================================================================
# 种子文档内容（来源：nsu.edu.cn 官网公开信息 + 招生简章 + 百科）
# =============================================================================

SEED_DOCUMENTS = [
    # ---- 规章制度类 ----
    {
        "title": "成都东软学院学校概况",
        "description": "成都东软学院基本信息、历史沿革、办学定位与特色介绍",
        "file_type": "md",
        "category_display": "规章制度",
        "college_display": None,  # 校级公开
        "content": """# 成都东软学院学校概况

## 基本信息

成都东软学院（Chengdu Neusoft University）是经国家教育部批准设立，由东软集团出资举办的一所民办全日制普通本科高等院校。学校坐落于四川省成都市都江堰市青城山镇东软大道1号，地处国家5A级旅游景区——都江堰-青城山风景区，校园呈现川西园林特色，环境优美。

- **创办时间**：2003年（前身为成都东软信息技术职业学院）
- **升格本科**：2011年5月，成为四川省第一所独立设置的民办普通本科高校
- **校长**：张应辉
- **校训**：精勤博学、学以致用
- **官网**：https://www.nsu.edu.cn
- **学校标识码**：4151012636
- **招生代码**（四川）：5177

## 办学规模

- **占地面积**：约52.85万平方米（近53万平方米）
- **建筑面积**：约36.33万平方米
- **在校生**：21000余人（2025年数据）
- **教职工**：1076人，其中专任教师815人，外聘教师398人
- **专任教师结构**：双师型教师137人（占16.81%），高级职称245人（占30.06%），硕博士学位754人（占92.52%）
- **学科门类**：工学、管理学、艺术学、文学、医学5大学科门类
- **本科专业**：30个（2025年）
- **累计毕业生**：3万余人，本科毕业生平均就业率94.5%

## 历史沿革

- **2002年**：成都东软信息技术职业学院在青城山麓奠基
- **2003年**：获批成立，被教育部确定为首批35所国家级示范性软件职业技术学院之一
- **2008年**：汶川大地震中学校距震中仅19公里，4600余名师生分别在大连和佛山异地复课
- **2011年**：升格为本科院校，更名"成都东软学院"
- **2012年**：成立博士后创新实践基地，入选四川省卓越工程师教育培养计划
- **2015年**：软件工程专业调整到本科第一批录取
- **2020年**：SOVO众创空间获批国家级众创空间
- **2021年**：通过教育部普通高等学校本科教学工作合格评估

## 办学特色

学校实施**TOPCARES一体化人才培养模式**，构建知识、能力、素养八大能力体系。采用"1321"特色学期设置（1学年3学期：2个理论学期+1个实践学期），强调"知识的运用比知识的拥有更重要"。

学校与华为、百度、腾讯、京东、阿里巴巴、微软、英特尔、IBM、SAP等众多知名企业建立了紧密合作关系，50%的专任教师具有产业经验，产教深度融合。

东软教育在成都、大连、佛山三地设有IT应用型本科高校，学生在读期间可申请跨校交换学习，收获异地学习经历。""",
    },

    # ---- 参考资料类 ----
    {
        "title": "成都东软学院院系与专业设置一览（2025）",
        "description": "13个二级学院及30个本科专业详细介绍，含各学院特色与就业方向",
        "file_type": "md",
        "category_display": "参考资料",
        "college_display": None,
        "content": """# 成都东软学院院系与专业设置一览（2025年）

## 一、计算机与软件学院

省级首批特色化示范性软件学院（2020年），网络安全产业学院为四川省首批现代产业学院（2022年）。

| 专业名称 | 层次 | 备注 |
|---------|------|------|
| 软件工程 | 本科 | 国家级一流本科专业建设点，一本招生 |
| 计算机科学与技术 | 本科 | 含本硕连读国际班 |
| 网络工程 | 本科 | 四川省一流本科专业建设点 |
| 物联网工程 | 本科 | 四川省一流本科专业建设点 |
| 网络空间安全 | 本科 | 配套网络攻防实验室 |

毕业生广泛就职于百度、阿里巴巴、字节跳动、美团、网易、奇安信、科大讯飞、SAP等知名IT企业。

## 二、智能科学与工程学院

拥有省级人工智能实验教学示范中心。

| 专业名称 | 层次 | 备注 |
|---------|------|------|
| 信息工程 | 本科 | |
| 数据科学与大数据技术 | 本科 | |
| 大数据管理与应用 | 本科 | |
| 人工智能 | 本科 | |
| 智能科学与技术 | 本科 | |
| 机器人工程 | 本科 | 2025年新增 |
| 数据科学与大数据技术（中外合作办学）| 本科 | 2025年新增，与美国东康涅狄格州立大学"3+1"合作 |

## 三、信息与商务管理学院

以"IT+管理"、"IT+商务"新工科/新文科融合为特色。

| 专业名称 | 层次 | 备注 |
|---------|------|------|
| 信息管理与信息系统 | 本科 | |
| 电子商务 | 本科 | |
| 财务管理 | 本科 | 含本硕连读国际班 |
| 人力资源管理 | 本科 | |
| 供应链管理 | 本科 | |

## 四、数字艺术与设计学院

国家火炬计划成都数字娱乐产业人才培训基地。拥有虚拟现实、元宇宙中心、动画电影等22个实验实训室和12个产学研用一体化工作室。参与制作《流浪地球2》《西游记之再世妖王》等商业电影。

| 专业名称 | 层次 | 类别 |
|---------|------|------|
| 数字媒体技术 | 本科 | 普通类 |
| 工业设计 | 本科 | 普通类 |
| 虚拟现实技术 | 本科 | 普通类 |
| 动画 | 本科 | 美术与设计类 |
| 艺术与科技 | 本科 | 美术与设计类 |
| 数字媒体艺术 | 本科 | 美术与设计类 |
| 视觉传达设计 | 本科 | 美术与设计类 |

## 五、外国语学院

以"外语+IT"、"外语+商务"、"外语+艺术"为培养特色。90%以上教师具有985/211及海外留学背景。

| 专业名称 | 层次 |
|---------|------|
| 商务英语 | 本科 |
| 英语 | 本科 |
| 日语 | 本科 |

## 六、健康医疗科技学院

以"IT+健康管理"、"IT+医疗科技"融合模式，拥有一所三级附属医院和多所三甲教学实习医院。

| 专业名称 | 层次 |
|---------|------|
| 健康服务与管理 | 本科 |
| 医学影像技术 | 本科 |
| 智能医学工程 | 本科 |
| 医学信息工程 | 本科 |

## 七、高等职业技术学院

| 专业名称 | 层次 |
|---------|------|
| 软件技术 | 专科 |
| 影视动画 | 专科 |

## 其他教学单位

基础教学学院、马克思主义学院、创新创业学院、国际教育学院、继续教育（培训）学院、退役军人教育学院。""",
    },

    # ---- 官网公告类 ----
    {
        "title": "成都东软学院2025年招生简章",
        "description": "2025年本科招生章程，含招生计划、录取规则、学费标准及奖助学金政策",
        "file_type": "md",
        "category_display": "官网公告",
        "college_display": None,
        "content": """# 成都东软学院2025年招生简章

## 学校基本信息

- 学校全称：成都东软学院（Chengdu Neusoft University）
- 办学性质：民办全日制普通本科高等院校
- 主管部门：四川省教育厅
- 招生代码（四川）：5177
- 院校标识码：4151012636
- 地址：四川省成都市都江堰市青城山镇东软大道1号（邮编：611844）

## 招生计划

2025年面向全国30个省（自治区、直辖市）招生（除西藏、港澳台外）。2025年新增"机器人工程"和"数据科学与大数据技术（中外合作办学）"两个本科专业；停招"市场营销"与"物流管理"两个专业。

### 六大专业群

1. **计算机与软件类**：软件工程、计算机科学与技术、网络工程、物联网工程、网络空间安全
2. **智能与电子工程类**：信息工程、数据科学与大数据技术、人工智能、智能科学与技术、机器人工程
3. **信息管理类**：信息管理与信息系统、电子商务、财务管理、人力资源管理、供应链管理
4. **数字艺术类**：数字媒体技术、工业设计、虚拟现实技术、动画、艺术与科技、数字媒体艺术、视觉传达设计
5. **健康医疗科技类**：健康服务与管理、医学影像技术、智能医学工程、医学信息工程
6. **外国语言类**：商务英语、英语、日语

## 录取规则

1. **录取原则**：分数优先，不设专业级差，按成绩和专业志愿择优录取
2. **调档比例**：平行志愿批次100%；顺序志愿批次不超过120%
3. **专业调剂**：所填志愿无法满足时，服从调配则分配至未满额专业，不服从则退档
4. **加分政策**：认可国家和各省规定的加分、降分政策
5. **外语语种**：不限；非外语专业公共必修课为大学英语，日语考生可申请修读大学日语
6. **男女比例**：不限
7. **转专业**：新生入校后可按规定申请，经考核审批后调整

## 艺术类专业录取规则

- 仅招收美术与设计类考生
- 须参加所在省美术类专业统考，文化与专业成绩双上线
- 四川省综合成绩 = 文化成绩×50% + 专业统考成绩×750/300×50%

## 学费标准

| 类型 | 学费（元/年） |
|------|--------------|
| 普通本科专业 | 18000～20000 |
| 中外合作办学（前三年） | 68000 |
| 艺术类专业 | 20000 |

## 奖助学金体系

| 奖项 | 金额（元/人·年） |
|------|-----------------|
| 华天奖学金 | 10000 |
| 东软睿新奖学金 | 一等奖5000 / 二等奖2000 / 三等奖1000 |
| 国家奖学金 | 10000 |
| 国家励志奖学金 | 6000 |
| 国家助学金 | 2500～4900 |

每年约30%在校生可获得各类奖助学金。生源地信用助学贷款最高20000元/年（在校期间国家贴息）。

## 中外合作办学

数据科学与大数据技术专业与美国东康涅狄格州立大学合作"3+1"培养模式：
- 前三年国内学费：68000元/年
- 第四年出国按美方学费标准（2024年约19940美元）
- 毕业获成都东软学院毕业证、学位证 + 东康涅狄格州立大学学位证

## 联系方式

- 📞 招生电话：028-64888001 / 64888002 / 64888003
- 📧 招生邮箱：edu@nsu.edu.cn
- 🌐 招生信息网：https://zs.nsu.edu.cn""",
    },

    # ---- 参考资料类 ----
    {
        "title": "成都东软学院校园生活指南",
        "description": "宿舍、食堂、快递、体育设施、周边环境等校园生活全攻略",
        "file_type": "md",
        "category_display": "参考资料",
        "college_display": None,
        "content": """# 成都东软学院校园生活指南

## 一、宿舍

### 基本配置
- 大部分为四人间，上床下桌，配备空调、独立卫浴（干湿分离）
- 少数D1-D3宿舍为公共卫浴
- 新校区（医学相关专业）与老校区距离很近，步行几分钟可达

### 宿舍设施
- 宿舍楼配备洗衣机、烘干机、共享吹风机、自动贩卖机
- 饮水机需自行购买（可室友合买）

### 供电时间
- 周中+周日：6:00-23:00
- 周五、周六：延长至23:30（空调不断电）

### 热水供应
- 早晨：6:00-8:00
- 中午：12:00-14:00
- 晚上：18:00-23:00

### 宿舍分布
- 男寝：D1-D3、E1、E3
- 女寝：D5-D9、E2（基本全独卫）
- 混合寝：D11-D12（男女分层）

## 二、食堂与美食

### 两大食堂
- **新食堂（B1）**：共三层，一楼早中晚餐，二楼午晚餐品种繁多，三楼有称斤自选菜、鸡公煲、旋转小火锅等特色餐饮
- **老食堂（A9）**：共三层，一楼早6点开餐，二三楼有水煮肉片、糖醋里脊等人气菜品

### 校外美食
- 老校区旁热闹美食街（干锅、炒菜、奶茶等）
- 新校区门口小吃街
- 周边"星光夜市"（烤苕皮、冰粉等川味小吃）
- 校内乐来佳（面食、炸鸡汉堡）、蜜雪餐厅（高性价比炒饭盖饭）

## 三、快递服务

校内设有两个菜鸟驿站、京东和邮政快递点，网购非常方便。

## 四、体育设施

- 老校区：多个篮球场、足球场、排球场、羽毛球场、乒乓球室
- 新校区：篮球场、网球场、羽毛球场
- 各类体育社团（轮滑社、跆拳道社、篮球社、滑板社、排球社）

## 五、网络与通讯

- 全校Wi-Fi覆盖
- 校园卡非强制办理，不办也可使用校内网参与选课
- 办卡可享受更优惠的流量套餐和校园宽带/WiFi

## 六、校园卡与学生证

- 校园卡用于食堂消费、图书馆借阅、门禁通行
- 学生证享受景区优惠（青城山、都江堰等）

## 七、军训安排

开学即训，时长约2周。训练时间：
- 上午：8:30-11:30
- 下午：14:30-16:00
- 晚上：19:00-21:30

训练量适中，注重纪律养成和团队协作。""",
    },

    # ---- 规章制度类 ----
    {
        "title": "成都东软学院奖助学金与资助政策",
        "description": "华天奖学金、东软睿新奖学金、国家奖助学金及助学贷款政策详解",
        "file_type": "md",
        "category_display": "规章制度",
        "college_display": None,
        "content": """# 成都东软学院奖助学金与资助政策

## 一、奖学金

### 华天奖学金
- 金额：10000元/人·年
- 评选标准：综合学业成绩、科研创新、社会实践
- 为学校最高荣誉奖学金

### 东软睿新奖学金
- 一等奖：5000元/人·年
- 二等奖：2000元/人·年
- 三等奖：1000元/人·年
- 评选标准：学业成绩排名、科技创新、学科竞赛获奖

### 国家奖学金
- 金额：10000元/人·年
- 评选标准：学业成绩特别优异，综合表现突出

### 国家励志奖学金
- 金额：6000元/人·年
- 评选标准：家庭经济困难且学业成绩优秀的学生

## 二、助学金

### 国家助学金
- 一等：4900元/人·年
- 二等：3500元/人·年
- 三等：2500元/人·年
- 评选标准：家庭经济困难认定等级

## 三、助学贷款

### 生源地信用助学贷款
- 最高额度：20000元/年
- 贷款期限：学制+15年，最长不超过22年
- 在校期间利息由国家财政全额贴息
- 毕业后开始还款，有5年还本宽限期

## 四、勤工助学

学校设置校内勤工助学岗位，学生可利用课余时间工作获得劳动报酬，岗位包括图书馆助理、实验室助理、行政助理等。

## 五、覆盖比例

每年约**30%**的在校生可获得各类奖助学金，确保不让任何一名学生因家庭经济困难而失学。

## 六、申请流程

1. 每学年开学初（9月）发布评选通知
2. 学生提交申请材料
3. 班级/年级评议小组初审
4. 学院评审工作组复审
5. 学校评审委员会终审
6. 公示无异议后发放

具体申请时间和材料要求以各学院通知为准。""",
    },

    # ---- 参考资料类 ----
    {
        "title": "华天图书馆使用指南",
        "description": "成都东软学院图书馆开放时间、馆藏资源、电子数据库及使用规则",
        "file_type": "md",
        "category_display": "参考资料",
        "college_display": None,
        "content": """# 华天图书馆使用指南

## 一、基本信息

华天图书馆是成都东软学院的标志性建筑，坐落于校园核心区域。馆内环境优越，设有咖啡店、文创空间，是师生学习、阅读和交流的重要场所。

## 二、开放时间

- 周一至周日：8:00-22:00
- 法定节假日开放时间另行通知（通常缩短开放时间）
- 寒暑假期间开放安排以图书馆公告为准

## 三、馆藏资源

截至2026年数据，馆藏纸质及电子文献资源总计达**330万册**，涵盖文、理、工、医、经、管等学科门类。

### 纸质资源
- 纸质图书：129.71万册（2024年数据）
- 以计算机、信息管理、动漫、艺术、多媒体为重点
- 覆盖经济学、心理学、医学等学科门类

### 电子资源数据库
- 中国知网（CNKI）
- 超星移动图书馆
- 万方医学网
- 各类电子期刊和电子图书数据库

## 四、借阅规则

- 本科生：最多借阅10册，借期30天，可续借1次（30天）
- 研究生：最多借阅15册，借期60天，可续借1次（30天）
- 教职工：最多借阅20册，借期90天，可续借1次（60天）
- 逾期归还按0.1元/册/天收取滞纳金

## 五、设施与服务

- 自习区（含空调、电源插座）
- 小组研讨室（需提前预约）
- 电子阅览区（配备电脑和网络）
- 自助借还机
- 打印/复印/扫描服务
- 咖啡店与文创空间

## 六、入馆须知

- 凭校园卡刷卡入馆
- 保持安静，手机调至静音模式
- 禁止携带食品和有色饮料入馆
- 个人物品自行保管，贵重物品随身携带

## 七、电子资源访问

校内通过校园网直接访问所有电子资源数据库。校外访问需通过VPN或CARSI认证登录。

VPN地址：https://vpn.nsu.edu.cn（需使用统一身份认证账号登录）""",
    },

    # ---- 官网公告类 ----
    {
        "title": "成都东软学院2025-2026学年校历与学期安排",
        "description": "学年学期时间安排、考试周、假期、毕业典礼及重要时间节点",
        "file_type": "md",
        "category_display": "官网公告",
        "college_display": None,
        "content": """# 成都东软学院2025-2026学年校历与学期安排

## 一、学年结构（"1321"特色学期制）

成都东软学院采用"1321"学期设置模式：**1个学年分为3个学期**：
- 2个基于案例和项目的理论学期
- 1个集中训练的实践学期

强调"知识的运用比知识的拥有更重要"。

## 二、2025-2026学年关键时间节点

### 秋季学期
- 开学报到：2025年9月初（具体日期以录取通知书为准）
- 新生军训：开学即训，时长约2周
- 上课开始：军训结束后
- 期末考试：2026年1月
- 寒假开始：2026年1月中旬

### 春季学期
- 开学：2026年2月底
- 期末考试：2026年6月-7月
- 毕业典礼：2026年6月中下旬
- 暑假开始：2026年7月中旬

## 三、假期安排

- **暑假**：7月中旬至8月底（约6周）
- **寒假**：1月中旬至2月底（约6周）
- **法定节假日**：按国家规定执行，含元旦、清明、五一、端午、中秋、国庆

## 四、上课时间表

### 上午
- 第1-2节：8:30-10:00
- 第3-4节：10:20-11:50

### 下午
- 第5-6节：14:00-15:30
- 第7-8节：15:50-17:20

### 晚上
- 第9-10节：19:00-20:30

每节课45分钟，课间休息10分钟。

## 五、重要提示

- 实践学期安排由各学院根据专业特点自主确定
- 三校跨校游学（成都、大连、佛山）需提前一学期申请
- 补考通常在开学后第2周进行
- 选课时间以教务处通知为准，一般在前一学期末进行

## 六、联系方式

- 教务处：028-64888000（教学运行、选课、成绩相关）
- 学生工作处：028-64888000转（奖助学金、宿舍、社团相关）""",
    },

    # ---- 新闻动态类 ----
    {
        "title": "成都东软学院学生社团全景介绍",
        "description": "涵盖科技、文艺、运动、实践、公益五大类学生社团信息",
        "file_type": "md",
        "category_display": "新闻动态",
        "college_display": None,
        "content": """# 成都东软学院学生社团全景介绍

社团分为一级社团（组织类）和二级社团（兴趣类），完全自愿加入。

## 一、科技类社团

### 计算机网络协会
面向计算机网络技术爱好者，组织网络技术培训、参加各类网络竞赛。

### 信息安全协会
专注网络安全领域，可参加CTF（Capture The Flag）竞赛、漏洞挖掘实战训练。

## 二、文艺类社团

- **米洛音乐社**：声乐、器乐培训与演出
- **锦繁动漫社**：动漫欣赏、Cosplay、同人创作
- **汉服社**：传统汉服文化推广、国学研习
- **NU-KPOP社**：K-POP舞蹈翻跳、韩流文化交流
- **问道茶社**：中国传统茶文化、茶艺研习

## 三、运动类社团

- 轮滑社
- 跆拳道社
- 篮球社
- 电竞社
- 滑板社
- 排球社

## 四、实践类社团

### 营销协会
对接校企合作项目，组织营销策划比赛。

### 创新创业社团
依托SOVO国家级众创空间，开展创业实践项目孵化。

## 五、公益类社团

- **心理协会**：心理健康知识普及、朋辈辅导
- **公益服务社**：社区志愿服务、支教助学活动

## 六、社团加入流程

1. 每学年开学初（9月）各社团集中招新（"百团大战"）
2. 填写报名表 → 面试（部分社团）→ 正式加入
3. 社团活动不收取会费（部分需自备器材）

## 七、社团荣誉

信息安全协会多次在省级CTF竞赛中获奖；米洛音乐社多次在大学生艺术展演中获奖；创新创业社团多个项目获国家级大学生创新创业训练计划立项。""",
    },

    # ---- 规章制度类 ----
    {
        "title": "成都东软学院录取规则与入学政策（2025）",
        "description": "招生录取规则、同分排序、体检要求以及新生入学转专业政策",
        "file_type": "md",
        "category_display": "规章制度",
        "college_display": None,
        "content": """# 成都东软学院录取规则与入学政策（2025年）

## 一、录取规则

### 基本原则
- **分数优先，不设专业级差**：按考生投档成绩和专业志愿顺序从高到低择优录取
- **平行志愿批次调档比例**：100%
- **顺序志愿批次调档比例**：不超过120%

### 专业调剂
- 所填报的所有专业志愿都无法满足时：
  - 服从专业调剂 → 调剂到尚未录满的专业
  - 不服从专业调剂 → 做退档处理

### 加分政策
- 认可国家和各省（自治区、直辖市）招生主管部门规定的加分、降分政策
- 加分后成绩作为录取和安排专业的依据

## 二、同分排序规则（3+1+2高考省份）

### 历史类
排序优先级：语文 → 历史 → 数学 → 外语

### 物理类
排序优先级：数学 → 物理 → 语文 → 外语

## 三、外语语种要求

- 所有专业**外语语种不限**
- 非外语专业公共必修外语课程为**大学英语**
- 高考外语语种为日语的考生，入学后可申请修读大学日语替代大学英语

## 四、体检要求

- 按教育部、卫生部、中国残疾人联合会印发的《普通高等学校招生体检工作指导意见》执行
- 艺术类专业（动画、艺术与科技、数字媒体艺术、视觉传达设计）要求**无色盲、色弱**
- 新生入学后将进行体检复查，不符合要求者按规定处理

## 五、转专业政策

新生入校后可按规定申请转专业，经考核审批同意后调整。一般在大一第一学期末开放申请，主要考核学业成绩和面试表现。艺术类与非艺术类专业之间不能互转；中外合作办学专业进校后不得转专业。

## 六、入学复查

新生入学后，学校在3个月内按照国家招生规定进行复查，包括录取手续及程序、录取资格、身份证明、身心健康状况、专业水平等。复查不合格者按相关规定处理。

## 七、毕业与学位

- 修完人才培养方案规定全部课程及实践环节，取得最低总学分，颁发成都东软学院毕业证书
- 符合学士学位授予条件的本科毕业生，授予成都东软学院学士学位证书
- 中外合作办学专业达到两校标准者，可同时获得美国东康涅狄格州立大学学位证""",
    },

    # ---- 新闻动态类 ----
    {
        "title": "成都东软学院实验室与科研平台介绍",
        "description": "82个实体实验室、省级实验教学示范中心、SOVO国家级众创空间等科研平台信息",
        "file_type": "md",
        "category_display": "新闻动态",
        "college_display": None,
        "content": """# 成都东软学院实验室与科研平台介绍

## 一、实验教学概况

- 实体实验室：82个
- 教学科研仪器设备总值：约1.22亿元
- 校外实习实训基地：193个
- 省级大学生校外实践基地：4个

## 二、省级实验教学平台

### 省级实验教学示范中心
1. **网络工程实验教学中心**：支撑网络工程、网络空间安全等专业实验教学
2. **人工智能实验教学示范中心**（智能科学与工程学院）：下设智能应用实验室、人工智能与并行计算实验室、人工智能创新实验室等

### 省级虚拟仿真实验教学中心
1. **网络工程虚拟仿真实验教学中心**：4A在线虚拟实验平台，支持远程实验操作

## 三、专业实验室（部分）

- 智能硬件实验室
- 智能可穿戴实验室
- 物联网实验室
- 信息安全实验室（含网络攻防实验平台）
- 虚拟现实实验室
- 电视演播室
- 动作捕捉实验室
- 动画及非线编实验室
- 物流实验室
- ERP实验室

## 四、SOVO国家级众创空间

大学生创业中心（SOVO）成立于2004年，2020年获批国家级众创空间，成为四川省第一所获此称号的民办高校。

SOVO将创新创业教育融入人才培养全过程，为学生提供：
- 创业项目孵化
- 企业导师指导
- 创业基金支持
- 创业培训与比赛

## 五、研究机构

- 博士后创新实践基地（2012年设立）
- 10个校级研究所
- 1个国际合作研究院

## 六、产教融合合作

学校与东软、华为、百度、腾讯、京东、阿里巴巴、微软、英特尔、IBM、SAP等企业建立紧密合作关系，共建实验室和实训基地，50%的专任教师具有产业经验。

## 七、数字艺术特色平台

数字艺术与设计学院拥有：
- 虚拟现实制作中心
- 元宇宙创新中心
- 动画电影制作中心
- 12个产学研用一体化工作室
- 团队参与制作《流浪地球2》、《西游记之再世妖王》等商业电影""",
    },
]


# =============================================================================
# 主逻辑
# =============================================================================

def _get_category_id(display_name):
    """根据显示名称查找分类ID。"""
    from apps.classify.models import Category
    try:
        cat = Category.objects.filter(name=display_name).first()
        return cat.id if cat else None
    except Exception:
        return None


def _get_college_id(display_name):
    """根据显示名称查找学院ID（None表示校级公开）。"""
    if display_name is None:
        return None
    from apps.accounts.models import College
    try:
        college = College.objects.filter(name__icontains=display_name).first()
        return college.id if college else None
    except Exception:
        return None


def _find_super_admin():
    """返回一个超级管理员用户（用于 doc.uploader），没有则返回 None。"""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.filter(role='super_admin').first()


def list_seed_docs():
    """列出所有已存在的种子文档。"""
    from apps.knowledge.models import KnowledgeDoc

    titles = [d['title'] for d in SEED_DOCUMENTS]
    docs = KnowledgeDoc.objects.filter(title__in=titles)

    if not docs.exists():
        print('没有已存在的种子文档。')
        return

    print(f'\n{"=":.60}')
    print(f'  已存在的种子文档（共 {docs.count()} 篇）')
    print(f'{"=":.60}')
    for doc in docs.order_by('id'):
        print(f'  #{doc.id:>4}  [{doc.status:<10}] {doc.title[:50]}')


def create_seed_docs(uploader, force=False):
    """创建种子文档（KnowledgeDoc 记录，status='uploaded'）。"""
    from apps.knowledge.models import KnowledgeDoc

    existing_titles = set(
        KnowledgeDoc.objects.filter(
            title__in=[d['title'] for d in SEED_DOCUMENTS]
        ).values_list('title', flat=True)
    )

    if existing_titles and not force:
        print(f'发现 {len(existing_titles)} 篇同名文档已存在。')
        print('使用 --force 强制重建，或使用 --list 查看已有文档。')
        return []

    # 强制重建：删除已有同名文档及其 chunks 和向量
    if force and existing_titles:
        from apps.knowledge.models import DocChunk
        from utils.milvus import milvus_client

        old_docs = KnowledgeDoc.objects.filter(title__in=list(existing_titles))
        old_ids = list(old_docs.values_list('id', flat=True))

        # 删除 DocChunks
        DocChunk.objects.filter(doc_id__in=old_ids).delete()
        # 删除 Milvus 向量
        try:
            milvus_client.connect()
            for did in old_ids:
                milvus_client.delete_by_doc_id(did)
        except Exception as e:
            logger.warning(f'删除 Milvus 向量时出错（可忽略）: {e}')
        # 删除 KnowledgeDoc
        deleted_count, _ = old_docs.delete()
        print(f'已删除 {deleted_count} 篇旧种子文档。')

    created_docs = []
    for item in SEED_DOCUMENTS:
        doc = KnowledgeDoc.objects.create(
            title=item['title'],
            description=item['description'],
            source_type='upload',
            file_type=item['file_type'],
            visibility='public',
            file_name=f"{item['title']}.md",
            file_size=len(item['content'].encode('utf-8')),
            status='uploaded',
            uploader=uploader,
            college_id=_get_college_id(item['college_display']),
            category_id=_get_category_id(item['category_display']),
        )
        # 将文档内容存到 OSS key 字段（当 OSS 不可用时作为备选内容存储）
        doc.oss_key = f"seed/{item['title']}.md"
        doc.save(update_fields=['oss_key'])
        created_docs.append((doc, item['content']))
        print(f'  创建文档 #{doc.id}: {doc.title[:50]}')

    return created_docs


def index_seed_docs(created_docs):
    """对已创建的种子文档执行索引（分块 → Embedding → Milvus + MySQL）。"""
    from django.utils import timezone
    from apps.knowledge.models import DocChunk, KnowledgeDoc
    from apps.knowledge.utils import chunk_document
    from utils.llm import llm_client
    from utils.milvus import milvus_client

    # 确保向量库就绪
    milvus_client.ensure_collection()

    total_start = time.time()
    success = 0
    fail = 0

    for i, (doc, content) in enumerate(created_docs, 1):
        print(f'\n[{i}/{len(created_docs)}] 索引: {doc.title[:50]}')
        doc_start = time.time()

        try:
            # 1. 分块
            doc.status = 'chunking'
            doc.save(update_fields=['status', 'updated_at'])

            raw_chunks = chunk_document(content, chunk_size=500, overlap=100)
            if not raw_chunks:
                print(f'  X 分块结果为空')
                doc.status = 'failed'
                doc.save(update_fields=['status', 'updated_at'])
                fail += 1
                continue
            print(f'  分块: {len(raw_chunks)} 块')

            # 2. Embedding
            doc.status = 'embedding'
            doc.save(update_fields=['status', 'updated_at'])

            texts = [c['content'] for c in raw_chunks]
            embeddings = llm_client.embed(texts)
            if not embeddings or len(embeddings) != len(raw_chunks):
                print(f'  X Embedding 失败或数量不匹配')
                doc.status = 'failed'
                doc.save(update_fields=['status', 'updated_at'])
                fail += 1
                continue
            print(f'  Embedding: {len(embeddings)} 条向量')

            # 3. 写入 Milvus
            milvus_chunks = []
            for j, c in enumerate(raw_chunks):
                milvus_chunks.append({
                    'vector_id': f'doc_{doc.id}_chunk_{j}',
                    'doc_id': doc.id,
                    'chunk_index': c['chunk_index'],
                    'chunk_text': c['content'],
                    'page_number': 0,
                    'seq_order': j,
                })

            vector_ids = milvus_client.insert_chunks(milvus_chunks, embeddings)
            print(f'  Milvus: {len(vector_ids)} 条写入')

            # 4. MySQL DocChunk
            doc_chunks = []
            for j, c in enumerate(raw_chunks):
                # 清洗 4 字节 Unicode 字符（MySQL utf8mb3 不支持）
                cleansed = _cleanse_for_mysql_utf8mb3(c['content'])
                doc_chunks.append(DocChunk(
                    doc=doc,
                    chunk_index=c['chunk_index'],
                    content=cleansed,
                    token_count=0,
                    vector_id=vector_ids[j] if j < len(vector_ids) else f'doc_{doc.id}_chunk_{j}',
                ))

            DocChunk.objects.bulk_create(doc_chunks, batch_size=100)
            print(f'  DocChunk: {len(doc_chunks)} 条写入')

            # 5. 更新状态
            doc.status = 'indexed'
            doc.chunk_count = len(raw_chunks)
            doc.indexed_at = timezone.now()
            doc.save(update_fields=['status', 'chunk_count', 'indexed_at', 'updated_at'])

            elapsed = time.time() - doc_start
            print(f'  OK 完成 ({elapsed:.1f}s, {len(raw_chunks)} 块)')
            success += 1

        except Exception as e:
            elapsed = time.time() - doc_start
            print(f'  X 失败 ({elapsed:.1f}s): {type(e).__name__}: {e}')
            try:
                doc.status = 'failed'
                doc.save(update_fields=['status', 'updated_at'])
            except Exception:
                pass
            fail += 1

    total_elapsed = time.time() - total_start
    print(f'\n{"=":.60}')
    print(f'  索引完成! 耗时: {total_elapsed:.1f}s')
    print(f'  成功: {success}  失败: {fail}')
    print(f'{"=":.60}')


def _cleanse_for_mysql_utf8mb3(text: str) -> str:
    """清除 4 字节 Unicode 字符（MySQL utf8mb3 无法存储的码点）。"""
    if not text:
        return text
    result = []
    for c in text:
        if ord(c) < 0x10000:
            result.append(c)
    return ''.join(result)


def ensure_vector_db():
    """确保向量数据库就绪。"""
    from utils.milvus import milvus_client

    logger.info('连接向量数据库...')
    connected = milvus_client.connect()
    if connected:
        logger.info('Milvus 已连接')
    else:
        logger.warning('Milvus 不可用，使用 ChromaDB 降级')

    ok = milvus_client.ensure_collection()
    if ok:
        logger.info('向量数据库就绪')
    else:
        logger.error('向量数据库初始化失败')
        sys.exit(1)


# =============================================================================
# CLI
# =============================================================================
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='种子测试文档生成脚本')
    parser.add_argument('--index', action='store_true', help='创建后立即索引')
    parser.add_argument('--force', action='store_true', help='强制重建（删除已有同名文档）')
    parser.add_argument('--list', action='store_true', help='仅列出已存在的种子文档')
    args = parser.parse_args()

    if args.list:
        list_seed_docs()
        sys.exit(0)

    # 查找上传者
    uploader = _find_super_admin()
    if not uploader:
        print('错误：未找到超级管理员用户，请先执行 scripts/create_admins.py')
        print('使用 --list 可查看已有文档。')
        sys.exit(1)

    print(f'上传者: {uploader.username} (id={uploader.id})')
    print(f'种子文档数量: {len(SEED_DOCUMENTS)}')
    print()

    # 创建文档
    created = create_seed_docs(uploader, force=args.force)
    if not created:
        print('没有新文档被创建。')
        sys.exit(0)

    print(f'\n共创建 {len(created)} 篇文档。')

    # 索引
    if args.index:
        ensure_vector_db()
        index_seed_docs(created)
    else:
        print('文档已创建（status=uploaded），使用 --index 执行索引，')
        print('或运行 python scripts/index_all_docs.py 批量索引。')
