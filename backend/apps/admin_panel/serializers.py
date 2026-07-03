"""Admin Panel - 用户管理序列化器"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from rest_framework import serializers

from apps.accounts.models import College


User = get_user_model()


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
        help_text='密码（必填）'
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        help_text='确认密码（必填）'
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
        # 防止越权：普通管理员不能把自己或超管升级
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
                raise serializers.ValidationError({'college': '学生必须设置学院'})
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
        help_text='新密码（必填）'
    )
    password_confirm = serializers.CharField(
        required=True,
        style={'input_type': 'password'},
        help_text='确认新密码（必填）'
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
        help_text='用户ID列表（最多100个）'
    )
    new_password = serializers.CharField(
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'},
        help_text='新密码（必填）'
    )
