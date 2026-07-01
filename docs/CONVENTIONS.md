# 校园虚拟资源管理系统 — 团队代码规范

> 基于项目现有模式提炼，"强制"条目所有成员必须遵守，"建议"条目尽量遵守。

---

## 目录

1. [项目结构](#1-项目结构)
2. [命名规范](#2-命名规范)
3. [导入顺序](#3-导入顺序)
4. [文档字符串与接口注释](#4-文档字符串与接口注释)
5. [模型规范](#5-模型规范)
6. [序列化器规范](#6-序列化器规范)
7. [视图规范](#7-视图规范)
8. [API 返回格式](#8-api-返回格式)
9. [错误处理](#9-错误处理)
10. [格式与 Lint](#10-格式与-lint)
11. [Git 提交规范](#11-git-提交规范)
12. [代码变更记录规范](#12-代码变更记录规范)
13. [数据库变更记录规范](#13-数据库变更记录规范)

---

## 1. 项目结构

```
backend/
├── config/           # Django 配置（settings/urls/wsgi/celery/全局权限）
├── apps/             # 业务应用（每个 app 一个目录）
│   ├── accounts/     # 用户 & 学院
│   ├── classify/     # 学科 & 资源分类
│   ├── resources/    # 资源 & 收藏
│   └── admin_panel/  # 管理后台
├── utils/            # 跨应用工具模块（llm/oss/milvus/rag/sse）
├── manage.py
└── .env              # 环境变量（不提交到 Git）
```

### 强制规则

- **新应用必须放在 `apps/` 下**，`startapp` 后手动移动到 `apps/xxx/`，并在 `AppConfig.name` 中写 `'apps.xxx'`
- **跨应用复用的代码放 `utils/`**，单应用内部工具放 `apps/xxx/utils.py`
- **敏感配置写 `.env`**，通过 `decouple.config()` 读取，[settings.py](backend/config/settings.py) 中提供 `default=` 回退值

---

## 2. 命名规范

### 强制规则

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | `CamelCase` | `class ResourceView`, `class IsSuperAdmin` |
| 函数/方法 | `snake_case` | `def get_queryset()`, `def compute_md5()` |
| 变量 | `snake_case` | `oss_client`, `view_count` |
| 模块级常量 | `UPPER_SNAKE_CASE` | `SYSTEM_PROMPT`, `STATUS_CHOICES` |
| 私有方法 | 前缀 `_` | `def _mock_embed()`, `def _format_sse()` |
| 模型字段 | `snake_case`，英文 | `oss_key`, `created_at`, `college_id` |
| 外键字段 | Django 默认：`表名_id` | `user_id`, `college_id`, `category_id` |
| 时间字段 | `created_at` / `updated_at` | 所有业务表统一使用 |
| `verbose_name` | **中文** | `verbose_name='学院名称'` |
| 数据库表名 | 小写英文 + 下划线分隔 | `college`, `resource`, `user_groups` |
| URL 路由名 | `snake_case` | `discipline_list`, `token_obtain_pair` |
| URL 路径 | 小写 + 尾部 `/` | `disciplines/`, `me/` |
| 迁移文件名 | Django 默认 | `0001_initial.py` |

### 建议

- 文件名用缩写而非后缀：[`llm.py`](backend/utils/llm.py) 优于 `llm_service.py`
- 避免拼音命名，所有标识符用英文单词

---

## 3. 导入顺序

### 强制规则

按以下 4 组分组，组间空一行：

```python
# 1. 标准库
import hashlib
import logging
from datetime import timedelta

# 2. Django 框架
from django.db import models
from django.conf import settings

# 3. 第三方包
from rest_framework import generics, permissions
import oss2

# 4. 本地模块
from .models import Resource
from .serializers import ResourceSerializer
from utils.llm import llm_client
```

### 例外

- **打破循环依赖**：可在函数体内部惰性导入模型（如 [`rag.py`](backend/utils/rag.py) 中的 `from apps.knowledge.models import KnowledgeDoc`）
- **配置块导入**：`settings.py` 底部 JWT 相关的 `from datetime import timedelta` 允许放在使用处附近

---

## 4. 文档字符串与接口注释

### 文档字符串规范

| 位置 | 语言 | 格式 | 示例 |
|------|------|------|------|
| 模块级 | **中文** | 多行，列出模块职责 | `"""\n阿里云 OSS 工具模块\n- STS 临时凭证签发\n- MD5 去重检查\n"""` |
| 模型类 | **中文** | 单行 | `"""二级学院（成都东软学院共13个）"""` |
| 公共函数/方法 | **中文** | 多行，说明参数/返回值 | 见下方示例 |
| 私有方法 | 可选 | 单行注释即可 | `# 内部：拼接完整 OSS 路径` |

公共函数文档示例：

```python
def generate_oss_key(original_name: str, user_id: int) -> str:
    """生成 OSS 对象存储 Key。

    Args:
        original_name: 原始文件名
        user_id: 上传用户 ID

    Returns:
        格式为 {user_id}/{uuid}/{safe_name} 的 OSS Key 字符串
    """
```

### 接口注释规范（强制）

**所有 API 接口必须添加注释，说明功能、参数、返回值：**

```python
class ResourceListView(generics.ListAPIView):
    """
    资源列表接口

    GET /api/resources/
    功能：分页返回资源列表，支持按学科、学院、状态筛选
    参数：
        - discipline_id (int, optional): 学科 ID
        - college_id (int, optional): 学院 ID
        - status (str, optional): 状态筛选
        - page (int, default=1): 页码
        - page_size (int, default=20): 每页数量
    返回：分页的资源列表，每条含标题、类型、上传者、时间等字段
    """
```

**ViewSet / 通用视图的最小注释要求：**

```python
class DisciplineListView(generics.ListAPIView):
    """
    学科方向列表

    GET /api/classify/disciplines/
    返回：所有学科方向，按 sort_order 排序
    """
```

### 建议

- AppConfig 的 `verbose_name` 用中文

---

## 5. 模型规范

### 强制规则

**主键：**

所有模型使用 Django 默认的 `BigAutoField` 作为主键（无需显式声明 `id` 字段）。

**CHOICES 定义：**

```python
class Resource(models.Model):
    STATUS_CHOICES = [
        ('draft', '草稿'),
        ('pending', '待审核'),
        ('published', '已发布'),
    ]
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='draft',
        db_index=True, verbose_name='状态'
    )
```

**时间字段：**

所有业务表统一使用以下两个时间字段：

```python
created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')
updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')
```

**软删除：**

需要逻辑删除的表添加以下两个字段：

```python
is_deleted = models.BooleanField(default=False, db_index=True, verbose_name='是否已删除')
deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='删除时间')
```

查询时必须在 Manager 或 View 层过滤 `is_deleted=False` 的记录。

**ForeignKey 声明（每个参数独立一行，固定顺序）：**

```python
uploader = models.ForeignKey(
    settings.AUTH_USER_MODEL,                     # 1. 目标模型
    on_delete=models.CASCADE,                     # 2. 删除策略
    related_name='resources',                     # 3. 反向关系名
    verbose_name='上传者'                          # 4. verbose_name（最后）
)
```

删除策略选用：
- `CASCADE` — 父记录删除时子记录也删除
- `PROTECT` — 有关联时禁止删除
- `SET_NULL` — 删除时置为 NULL（需配合 `null=True`）

**Meta 类固定顺序：**

```python
class Meta:
    db_table = 'resource'                              # 必须指定，小写下划线格式
    verbose_name = '资源'
    verbose_name_plural = verbose_name                  # 单复数相同时复用变量
    ordering = ['-created_at']
    indexes = [
        models.Index(fields=['status', 'created_at'], name='idx_res_status_created'),
    ]
```

**`db_table` 命名规则：**

- 统一使用小写字母 + 下划线格式
- 不与 Django 内置表名冲突
- 示例：`user_groups`, `user_user_permissions`, `django_admin_log`

**`__str__` 必须定义：**

```python
def __str__(self):
    return self.name                          # 简单模型直接返回字段
    return f'{self.name}（{self.category}）'   # 复合模型用 f-string
```

**权限角色体系：**

系统采用三级角色，在 [`accounts/models.py`](backend/apps/accounts/models.py) 中统一定义：

```python
ROLE_CHOICES = [
    ('super_admin', '超级管理员'),
    ('admin', '管理员'),
    ('user', '普通用户'),
]
```

| 角色 | 权限范围 |
|------|---------|
| `super_admin` | 全部权限，可管理所有资源与用户 |
| `admin` | 可管理自己的资源 + 管理 `user` 角色用户 |
| `user` | 仅浏览/下载已发布资源 |

---

## 6. 序列化器规范

### 强制规则

```python
from rest_framework import serializers
from .models import Resource


class ResourceSerializer(serializers.ModelSerializer):
    """资源序列化器"""

    # 关联字段展平（只读）
    college_name = serializers.CharField(source='college.name', read_only=True)
    uploader_name = serializers.CharField(source='uploader.username', read_only=True)

    class Meta:
        model = Resource
        fields = [
            'id', 'title', 'description', 'file_type',
            'file_size', 'oss_url', 'status',
            'college', 'college_name',
            'uploader', 'uploader_name',
            'view_count', 'download_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'view_count', 'download_count', 'created_at', 'updated_at']
```

- **用 `fields` 列出字段，不用 `exclude`**
- **关联展平字段用 `source=` 声明**，命名规则：`{外键字段名}_{目标字段名}`（如 `college_name`）
- **树形结构用 `SerializerMethodField`**（参考 [`Category` 序列化器](backend/apps/classify/serializers.py)）

---

## 7. 视图规范

### 强制规则

- **优先使用 DRF 通用类视图**：`ListAPIView`, `RetrieveAPIView`, `ListCreateAPIView`, `RetrieveUpdateDestroyAPIView`
- **权限列表显式声明**
- **每个视图必须添加文档字符串**，说明功能、参数、返回值（参见 [第 4 节](#4-文档字符串与接口注释)）

```python
class ResourceListView(generics.ListAPIView):
    """
    资源列表接口

    GET /api/resources/
    功能：分页返回资源列表，支持按学科、学院、状态筛选
    参数：
        - discipline_id (int, optional): 学科方向 ID
        - college_id (int, optional): 学院 ID
        - status (str, optional): 状态筛选（draft/pending/published/archived）
    返回：分页的资源列表，每条包含标题、类型、上传者、统计等字段
    """
    queryset = Resource.objects.select_related('college', 'uploader').all()
    serializer_class = ResourceListSerializer
    permission_classes = [permissions.IsAuthenticated]
```

- 需要自定义过滤/查询逻辑时覆盖 `get_queryset()`，不要直接给 `queryset` 赋值动态查询
- **URL 注册用 `.as_view()`**，避免使用 ViewSet + Router（项目未引入 Router 模式）

```python
# urls.py
urlpatterns = [
    path('', ResourceListView.as_view(), name='resource_list'),
    path('<int:pk>/', ResourceDetailView.as_view(), name='resource_detail'),
]
```

---

## 8. API 返回格式

### 强制规则

**成功响应：**

```json
// 返回单个对象
{
    "id": 1,
    "title": "计算机网络课件",
    "status": "published"
}

// 返回列表（分页）
{
    "count": 42,
    "next": "http://127.0.0.1:8000/api/resources/?page=2",
    "previous": null,
    "results": [...]
}

// 操作成功（无返回数据）
{"detail": "操作成功"}
```

**错误响应：**

```json
{
    "detail": "错误描述",
    "code": "ERROR_CODE"
}
```

**错误码规范：**

| HTTP 状态码 | 场景 | code 示例 |
|------------|------|-----------|
| 400 | 参数校验失败 | `VALIDATION_ERROR` |
| 401 | 未认证 | `UNAUTHORIZED` |
| 403 | 无权限 | `PERMISSION_DENIED` |
| 404 | 资源不存在 | `NOT_FOUND` |
| 409 | 资源冲突（重复） | `CONFLICT` |
| 500 | 服务器内部错误 | `INTERNAL_ERROR` |

---

## 9. 错误处理

### 强制规则

| 层级 | 策略 |
|------|------|
| **DRF 视图** | 不写 try/except，依赖 DRF 内置异常处理（自动转 400/403/404/500） |
| **序列化器** | 复杂校验用 `validate_xxx()` 方法，抛 `serializers.ValidationError` |
| **工具模块** | 外部服务调用必须捕获异常并 **记录日志**，不可静默吞掉 |

工具模块错误处理模板：

```python
import logging
logger = logging.getLogger(__name__)


class ExternalServiceClient:
    def do_something(self, param: str) -> dict | None:
        try:
            resp = requests.post(url, json={'param': param}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f'服务调用失败: {e}')
            return None                           # 降级返回，上层判断
```

### 禁止

- ❌ **裸 `except:` 或 `except Exception: pass`** — 必须记录日志
- ❌ 在视图中直接 `try/except` 返回 Response — 交给 DRF 处理

---

## 10. 格式与 Lint

### 强制规则

- **行宽上限 120 字符**
- **缩进 4 空格**（不用 Tab）
- **文件末尾留一个空行**
- **使用 UTF-8 编码**
- **换行符 LF**（`\n`）

### 配置文件

项目根目录已有 [`.editorconfig`](.editorconfig)：

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true

[*.py]
indent_style = space
indent_size = 4
max_line_length = 120

[*.{json,yml,yaml}]
indent_style = space
indent_size = 2

[*.md]
trim_trailing_whitespace = false
```

### 建议

- 安装 `ruff` 或 `flake8` 做自动检查
- PR 前运行 `python manage.py check --deploy` 确认配置正确

---

## 11. Git 提交规范

### 强制规则

提交信息格式：

```
<type>: <简短描述>

<详细说明（可选）>
```

**type 取值：**

| Type | 场景 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `refactor` | 重构（不改功能） |
| `docs` | 文档变更 |
| `style` | 格式调整（空格、换行，不动逻辑） |
| `chore` | 杂项（依赖更新、配置调整） |
| `test` | 测试相关 |

示例：
```
feat: 添加资源全文搜索接口

- 接入 Milvus 向量检索
- 支持按关键词 + 学科筛选
```

### 建议

- 一个 commit 做一件事
- 提交前确认 `python manage.py check` 通过

---

## 12. 代码变更记录规范

每次提交代码后，必须在项目根目录的 [`code-changes/`](code-changes/) 文件夹中新增或更新对应的变更记录文件。

### 强制规则

**文件命名：**

按日期命名，格式为 `YYYY-MM-DD.md`。同一天的所有代码变更合并到同一个文件中。

```
code-changes/
└── 2026-06-30.md
```

❌ 禁止使用中文文件名或空格。

**文件模板：**

```markdown
# 代码修改记录 — YYYY-MM-DD

> **需求**：<一句话描述本次修改的目标>
> **修改人**：<Git 用户名>

---

## 一、删除代码范围

<列出被删除的文件或代码块，无则写"无">

| 文件 | 说明 |
|------|------|
| `path/to/deleted.py` | 删除原因 |

---

## 二、新增代码范围

### <模块名> (<路径>)

| 文件 | 说明 |
|------|------|
| `path/to/file.py` | 文件作用描述 |

---

## 三、数据库变更

<如果本次代码变更涉及数据库表结构变化，在此列出并链接到对应的数据库变更记录；无则写"无">

| 表名 | 操作 | 记录文件 |
|------|------|----------|
| `table_name` | 新建/修改/删除 | [database-changes/table_name.md](../database-changes/table_name.md) |

---

## 四、Migration 文件

<列出本次产生的 Django Migration 文件路径，无则写"无">

- `apps/xxx/migrations/000N_xxx.py`
```

### 强制要求

- **每次提交代码后必须更新**：有代码变更就必须有对应的记录
- **模块拆分清晰**：每个业务模块独立一个小节（如 2.1、2.2），方便其他成员快速定位
- **数据库变更联动**：如果代码变更涉及表结构变化，必须在"三、数据库变更"中链接到对应的数据库变更记录文件
- **Migration 文件必列**：Django 项目必须列出本次产生的所有 migration 文件路径

---

## 13. 数据库变更记录规范

每次数据库表结构发生变化（新建表、修改字段、新增索引等），必须在项目根目录的 [`database-changes/`](database-changes/) 文件夹中新增或更新对应表的变更记录文件。

### 强制规则

**文件命名：**

按表名命名，格式为 `<table_name>.md`。每个数据库表对应一个独立的 `.md` 文件，该表的所有历史变更追加到同一文件中。

```
database-changes/
├── college.md
├── user.md
├── discipline.md
├── category.md
├── resource.md
└── favorite.md
```

❌ 禁止使用中文文件名或空格。

**文件模板（首次建表）：**

```markdown
# <table_name> 表

> **变更类型**：表创建（首次）
> **变更日期**：YYYY-MM-DD HH:MM:SS
> **变更人员**：<Git 用户名>
> **变更原因**：<说明为什么需要创建这张表>
> **影响范围**：<说明对现有数据和业务的影响>
> **回滚方案**：DROP TABLE IF EXISTS <table_name>;

\`\`\`sql
CREATE TABLE `<table_name>` (
    `id` BIGINT AUTO_INCREMENT NOT NULL PRIMARY KEY,
    ...字段定义...
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX `idx_xxx` ON `<table_name>`(`column_name`);
\`\`\`
```

**文件模板（表结构变更）：**

当表已存在、需要追加修改时，在文件末尾追加以下内容：

```markdown
---

## 变更 #N：<变更简述>

> **变更类型**：字段新增 / 字段修改 / 索引新增 / 表删除
> **变更日期**：YYYY-MM-DD HH:MM:SS
> **变更人员**：<Git 用户名>
> **变更原因**：<说明为什么需要做这个变更>
> **影响范围**：<说明对现有数据和业务的影响>
> **回滚方案**：<写出具体的回滚 SQL>

\`\`\`sql
ALTER TABLE `<table_name>` ADD COLUMN `new_field` VARCHAR(64) NOT NULL DEFAULT '';
\`\`\`
```

### 强制要求

- **一个表一个文件**：同一张表的所有变更（创建 + 后续修改）都在同一个 `.md` 文件中
- **后续变更追加**：表已存在时，在文件末尾用 `---` 分隔后追加新的变更记录，编号递增
- **变更类型明确**：必须注明是"字段新增/字段修改/索引新增/表删除"等具体操作类型
- **回滚方案必填**：每项变更必须提供回滚 SQL，确保出问题时可以快速恢复
- **与代码变更联动**：代码变更记录的"三、数据库变更"中必须链接到对应的数据库变更记录文件

---

## 附录：快速检查清单

每个 PR 自查：

- [ ] 新应用放在 `apps/` 下
- [ ] `verbose_name` 写中文
- [ ] 模型 `__str__` 已定义
- [ ] 时间字段使用 `created_at` / `updated_at`
- [ ] 外键字段使用 `表名_id` 命名
- [ ] `db_table` 指定表名，小写下划线格式
- [ ] 序列化器用 `fields` 不用 `exclude`
- [ ] 每个视图有文档字符串（功能/参数/返回值）
- [ ] API 返回格式符合统一规范
- [ ] 没有 `except: pass` 裸异常
- [ ] 导入按 标准库 → Django → 第三方 → 本地 顺序
- [ ] 角色使用 `super_admin` / `admin` / `user`
- [ ] 敏感配置走 `.env`
- [ ] 代码变更已记录到 `code-changes/YYYY-MM-DD.md`
- [ ] 数据库变更已记录到 `database-changes/<table_name>.md`（含回滚方案）
