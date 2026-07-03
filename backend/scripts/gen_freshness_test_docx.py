"""
生成"AI 提取发布日期"接口的端到端测试 Word 文档。

设计意图（对照 apps/knowledge/extractor.py 的 _EXTRACT_PROMPT 优先级）：
- 正文前部放"封面区"：包含明确的"修订日期"，是 LLM 优先级 1
- 中段放"适用学期"：是优先级 2（学年学期表达，prompt 要求取起始年第一天）
- 后段放一个干扰项（早期 v0.x 的过期日期），验证 LLM 不会取它
- 留出占位：方便人工补充截图/口头讲解

输出：docs/test-docx/AI_Freshness_Extract_Test_2026-07-03.docx
"""
from pathlib import Path
from datetime import datetime

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH


def build_doc(output_dir: Path) -> Path:
    doc = Document()

    # ----- 封面区 -----
    title = doc.add_heading('计算机学院 2025 秋季学期《软件工程》教学大纲', level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run('文档编号：').bold = True
    meta.add_run('SE-SYLL-2025-FA-007\n')
    meta.add_run('修订日期：').bold = True
    meta.add_run('2025-08-28\n')
    meta.add_run('版本：').bold = True
    meta.add_run('v2.3\n')
    meta.add_run('编写人：').bold = True
    meta.add_run('张三 / 李四\n')
    meta.add_run('审批人：').bold = True
    meta.add_run('王五（计算机学院副院长）\n')

    doc.add_paragraph().add_run('（封面说明：本页为 LLM 提取发布日期的优先级 1 来源，'
                                '期望被识别为"修订日期 2025-08-28"）').italic = True

    # ----- 目录占位 -----
    doc.add_heading('目录', level=1)
    for item in ['一、课程基本信息', '二、适用对象与学期',
                 '三、教学目标', '四、教学进度安排', '五、考核方式', '六、修订历史']:
        doc.add_paragraph(item)

    # ----- 一、课程基本信息 -----
    doc.add_heading('一、课程基本信息', level=1)
    doc.add_paragraph('课程名称：软件工程')
    doc.add_paragraph('课程编号：SE-301')
    doc.add_paragraph('学分：4')
    doc.add_paragraph('学时：64（理论 32 + 实验 32）')
    doc.add_paragraph('先修课程：数据结构、面向对象程序设计')

    # ----- 二、适用对象与学期 -----
    doc.add_heading('二、适用对象与学期', level=1)
    doc.add_paragraph('本课程适用于 2025-2026 学年第一学期，面向计算机科学与技术、'
                      '软件工程、人工智能三个专业的本科三年级学生开设。')
    p = doc.add_paragraph()
    p.add_run('（LLM 优先级 2 来源：学年学期表达，prompt 要求取起始年第一天，'
              '即 2025-09-01）').italic = True

    # ----- 三、教学目标 -----
    doc.add_heading('三、教学目标', level=1)
    for line in [
        '掌握软件工程的基本概念、生命周期模型与开发方法论。',
        '熟悉需求分析、设计、实现、测试、维护等核心环节。',
        '能够使用 Git、Docker、Jenkins 等工具完成团队协作开发。',
        '具备规范化编写技术文档与项目报告的能力。',
    ]:
        doc.add_paragraph(line, style='List Bullet')

    # ----- 四、教学进度安排 -----
    doc.add_heading('四、教学进度安排', level=1)
    table = doc.add_table(rows=1, cols=4)
    table.style = 'Light Grid Accent 1'
    hdr = table.rows[0].cells
    hdr[0].text = '周次'
    hdr[1].text = '日期'
    hdr[2].text = '内容'
    hdr[3].text = '授课教师'
    rows = [
        ('1',  '2025-09-01', '软件工程概述',         '张三'),
        ('2',  '2025-09-08', '需求工程',             '张三'),
        ('3',  '2025-09-15', '结构化分析',           '李四'),
        ('4',  '2025-09-22', '面向对象分析 UML',     '李四'),
        ('5',  '2025-09-29', '软件体系结构',         '王五'),
        ('6',  '2025-10-06', '国庆放假',             '—'),
        ('7',  '2025-10-13', '设计模式',             '王五'),
        ('8',  '2025-10-20', '期中考试',             '—'),
        ('9',  '2025-10-27', '编码规范与 Code Review','张三'),
        ('10', '2025-11-03', '单元测试',             '李四'),
        ('11', '2025-11-10', '持续集成 / CD',        '王五'),
        ('12', '2025-11-17', 'DevOps 实践',          '王五'),
        ('13', '2025-11-24', '项目答辩',             '张三'),
        ('14', '2025-12-01', '课程总结',             '张三'),
    ]
    for r in rows:
        cells = table.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v

    # ----- 五、考核方式 -----
    doc.add_heading('五、考核方式', level=1)
    doc.add_paragraph('平时成绩 30% + 期中考试 20% + 期末项目 50%。')

    # ----- 六、修订历史 -----
    doc.add_heading('六、修订历史', level=1)
    rev = doc.add_table(rows=1, cols=3)
    rev.style = 'Light Grid Accent 1'
    hdr = rev.rows[0].cells
    hdr[0].text = '版本'
    hdr[1].text = '日期'
    hdr[2].text = '说明'
    revs = [
        ('v1.0', '2023-08-30', '初版编写。'),
        ('v1.5', '2024-08-25', '调整实验课时比例。'),
        ('v2.0', '2025-02-15', '新增 DevOps 章节。'),
        ('v2.3', '2025-08-28', '本版本，按 2025-2026 学年第一学期修订。'),
    ]
    for r in revs:
        cells = rev.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = v

    # ----- 干扰项：故意放一个早期过期日期 -----
    doc.add_heading('附：历史归档说明', level=1)
    doc.add_paragraph('本大纲首版于 2021 年 9 月发布，彼时课程编号为 SE-200，'
                      '2022 年 4 月曾进行过一次小修订。'
                      '（干扰项：这两个早期日期均为历史归档信息，'
                      'LLM 不应被它们误导，仍应优先取封面修订日期 2025-08-28）')

    # ----- 页脚区 -----
    doc.add_paragraph()
    foot = doc.add_paragraph()
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    foot.add_run('计算机学院 · 软件工程教研室').bold = True

    # 文档属性里的"创建日期"也设成期望日期（部分解析器会读 docProps）
    doc.core_properties.created = datetime(2025, 8, 28, 9, 0, 0)
    doc.core_properties.modified = datetime(2025, 8, 28, 9, 0, 0)
    doc.core_properties.title = '计算机学院 2025 秋季学期《软件工程》教学大纲'
    doc.core_properties.author = '张三'

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / 'AI_Freshness_Extract_Test_2026-07-03.docx'
    doc.save(out_path)
    return out_path


if __name__ == '__main__':
    # 默认输出到 backend/docs/test-docx/（沿用项目 docs 习惯）
    here = Path(__file__).resolve().parent
    out_dir = here.parent / 'docs' / 'test-docx'
    path = build_doc(out_dir)
    print(f'OK: {path}')
    print(f'size: {path.stat().st_size} bytes')