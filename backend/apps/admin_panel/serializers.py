"""Admin Panel - 序列化器

分两组：
- 用户管理（既有）：UserAdminSerializer / UserCreateSerializer / UserUpdateSerializer /
  CollegeSerializer / ResetPasswordSerializer / BatchResetPasswordSerializer
- 超管日志管理（新增）：AdminUploadLogSerializer / AdminQueryLogSerializer /
  AdminSensitiveLogSerializer / AdminLoginLogSerializer / AdminOperationLogSerializer +
  4 个 Stats Serializer
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from rest_framework import serializers

from apps.accounts.models import College
from apps.chat.models import QueryLog, SensitiveLog
from apps.resources.models import UploadLog
from apps.resources.serializers import UploadLogSerializer

from .models import LoginLog, OperationLog


User = get_user_model()


# ======================================================================
# 一、用户管理（既有，未改动）
# ======================================================================
class UserAdminSerializer(serializers.ModelSerializer):
    """管理后台用户详情序列化器"""
    college_name = serializers.CharField(source='college.name', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    resource_count = serializers.IntegerField(source='resources.count', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role', 'role_display',
            'college', 'college_name',
            'department', 'department_name',
            'phone', 'avatar', 'is_active', 'date_joined',
            'last_login', 'resource_count',
        ]
        read_only_fields = [
            'id', 'username', 'date_joined', 'last_login', 'resource_count',
            'role_display', 'college_name', 'department_name',
        ]


class UserCreateSerializer(serializers.ModelSerializer):
    """新建用户序列化器"""
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'},
        help_text='密码（必填）',
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        help_text='确认密码（必填）',
    )

    class Meta:
        model = User
        fields = [
            'username', 'email', 'password', 'password_confirm',
            'first_name', 'last_name', 'role', 'college', 'department',
            'phone', 'avatar',
        ]

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password_confirm': '两次输入的密码不一致'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """编辑用户序列化器"""

    class Meta:
        model = User
        fields = [
            'email', 'first_name', 'last_name', 'role',
            'college', 'department',
            'phone', 'avatar', 'is_active',
        ]

    def validate_role(self, value):
        request_user = self.context['request'].user
        if value == 'super_admin' and request_user.role != 'super_admin':
            raise serializers.ValidationError('只有超级管理员可以设置超级管理员角色')
        return value

    def validate(self, attrs):
        """组合校验：role × college × department"""
        role = attrs.get('role', getattr(self.instance, 'role', None))
        college = attrs.get('college', getattr(self.instance, 'college', None))
        department = attrs.get('department', getattr(self.instance, 'department', None))

        if role == 'college_admin':
            if college is None:
                raise serializers.ValidationError({'college': '学院管理员必须设置学院'})
            if department is not None:
                raise serializers.ValidationError({'department': '学院管理员不能设置部门'})
        elif role == 'dept_admin':
            if department is None:
                raise serializers.ValidationError({'department': '部门管理员必须设置部门'})
            if college is not None:
                raise serializers.ValidationError({'college': '部门管理员不能设置学院'})
        elif role == 'user':
            if college is None:
                raise serializers.ValidationError({'college': '学生必须设置所属学院'})
        return attrs


class CollegeSerializer(serializers.ModelSerializer):
    """学院列表序列化器"""

    class Meta:
        model = College
        fields = ['id', 'name', 'code', 'sort_order']


class ResetPasswordSerializer(serializers.Serializer):
    """重置密码序列化器"""
    new_password = serializers.CharField(
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'},
        help_text='新密码（必填）',
    )
    password_confirm = serializers.CharField(
        required=True,
        style={'input_type': 'password'},
        help_text='确认新密码（必填）',
    )

    def validate(self, attrs):
        if attrs['new_password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password_confirm': '两次输入的密码不一致'})
        return attrs


class BatchResetPasswordSerializer(serializers.Serializer):
    """批量重置密码序列化器"""
    user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
        max_length=100,
        help_text='用户ID列表（最多100个）',
    )
    new_password = serializers.CharField(
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'},
        help_text='新密码（必填）',
    )


# ======================================================================
# 二、超管日志管理（新增）
# ======================================================================
class AdminUploadLogSerializer(UploadLogSerializer):
    """上传日志 - 超管视图（直接复用 resources 的 UploadLogSerializer）"""


class AdminQueryLogSerializer(serializers.ModelSerializer):
    """查询日志 - 超管视图"""

    user_name = serializers.CharField(
        source='user.username', read_only=True, allow_null=True,
    )

    class Meta:
        model = QueryLog
        fields = [
            'id', 'user', 'user_name',
            'conversation',
            'question', 'parsed_filters', 'hit_count',
            'top_doc_ids', 'response_ms', 'user_feedback',
            'created_at',
        ]
        read_only_fields = fields


class AdminSensitiveLogSerializer(serializers.ModelSerializer):
    """敏感内容日志 - 超管视图"""

    user_name = serializers.CharField(
        source='user.username', read_only=True, allow_null=True,
    )

    class Meta:
        model = SensitiveLog
        fields = [
            'id', 'user', 'user_name',
            'question', 'matched_rules', 'matched_texts',
            'refuse_message', 'created_at',
        ]
        read_only_fields = fields


class AdminLoginLogSerializer(serializers.ModelSerializer):
    """登录日志 - 超管视图"""

    user_name = serializers.CharField(
        source='user.username', read_only=True, allow_null=True,
    )
    login_type_display = serializers.CharField(
        source='get_login_type_display', read_only=True,
    )

    class Meta:
        model = LoginLog
        fields = [
            'id', 'user', 'user_name', 'username',
            'login_type', 'login_type_display',
            'success', 'fail_reason',
            'ip_address', 'user_agent',
            'created_at',
        ]
        read_only_fields = fields


class AdminOperationLogSerializer(serializers.ModelSerializer):
    """操作日志 - 超管视图"""

    operator_name = serializers.CharField(
        source='operator.username', read_only=True, allow_null=True,
    )
    action_display = serializers.CharField(
        source='get_action_display', read_only=True,
    )
    target_type_display = serializers.CharField(
        source='get_target_type_display', read_only=True,
    )

    class Meta:
        model = OperationLog
        fields = [
            'id', 'operator', 'operator_name', 'operator_username',
            'action', 'action_display',
            'target_type', 'target_type_display',
            'target_id', 'target_repr',
            'after', 'ip_address', 'user_agent',
            'created_at',
        ]
        read_only_fields = fields


# ----------------------------------------------------------------------
# Stats 序列化器（视图组装数据后落库）
# ----------------------------------------------------------------------
class _GroupCountSerializer(serializers.Serializer):
    """单条 by_xxx 分组的统一结构"""

    key = serializers.CharField()
    label = serializers.CharField(required=False, allow_blank=True)
    count = serializers.IntegerField()


class LogStatsBaseSerializer(serializers.Serializer):
    """通用日志统计公共字段"""

    total = serializers.IntegerField()
    today = serializers.IntegerField()
    this_week = serializers.IntegerField()
    this_month = serializers.IntegerField()


class UploadLogStatsSerializer(LogStatsBaseSerializer):
    """上传日志统计"""

    by_status = serializers.ListField(child=_GroupCountSerializer())
    by_file_type = serializers.ListField(child=_GroupCountSerializer())
    by_college = serializers.ListField(child=_GroupCountSerializer())
    total_size = serializers.IntegerField()


class QueryLogStatsSerializer(LogStatsBaseSerializer):
    """查询日志统计"""

    avg_hit_count = serializers.FloatField()
    avg_response_ms = serializers.FloatField()
    like_rate = serializers.FloatField()
    by_feedback = serializers.ListField(child=_GroupCountSerializer())


class LoginLogStatsSerializer(LogStatsBaseSerializer):
    """登录日志统计"""

    success_count = serializers.IntegerField()
    fail_count = serializers.IntegerField()
    by_success = serializers.ListField(child=_GroupCountSerializer())
    by_type = serializers.ListField(child=_GroupCountSerializer())
    by_hour = serializers.ListField(child=_GroupCountSerializer())


class OperationLogStatsSerializer(LogStatsBaseSerializer):
    """操作日志统计"""

    by_action = serializers.ListField(child=_GroupCountSerializer())
    by_target_type = serializers.ListField(child=_GroupCountSerializer())
    top_operators = serializers.ListField(child=_GroupCountSerializer())
