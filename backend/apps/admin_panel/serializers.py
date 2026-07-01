"""Admin Panel - 用户管理序列化器"""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


User = get_user_model()


class UserAdminSerializer(serializers.ModelSerializer):
    """管理后台用户详情序列化器"""
    college_name = serializers.CharField(source='college.name', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    resource_count = serializers.IntegerField(source='resources.count', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role', 'role_display', 'college', 'college_name',
            'phone', 'avatar', 'is_active', 'date_joined',
            'last_login', 'resource_count',
        ]
        read_only_fields = ['id', 'date_joined', 'last_login', 'resource_count']


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
            'first_name', 'last_name', 'role', 'college',
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
            'email', 'first_name', 'last_name', 'role', 'college',
            'phone', 'avatar', 'is_active',
        ]

    def validate_role(self, value):
        request_user = self.context['request'].user
        # 防止越权：普通管理员不能把自己或超管升级
        if value == 'super_admin' and request_user.role != 'super_admin':
            raise serializers.ValidationError('只有超级管理员可以设置超级管理员角色')
        return value
