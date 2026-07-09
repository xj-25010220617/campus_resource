from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """用户信息序列化器"""
    college_name = serializers.CharField(source='college.name', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    role_display = serializers.CharField(source='get_role_display', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role', 'role_display', 'college', 'college_name',
            'department', 'department_name',
            'phone', 'avatar', 'date_joined',
        ]
        read_only_fields = [
            'id', 'username', 'role', 'role_display',
            'college', 'college_name', 'department', 'department_name',
            'date_joined',
        ]

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        role = instance.role
        # 按角色只保留对应的归属字段，避免暴露矛盾数据
        if role == 'dept_admin':
            ret.pop('college', None)
            ret.pop('college_name', None)
        elif role in ('college_admin', 'user'):
            ret.pop('department', None)
            ret.pop('department_name', None)
        # super_admin / admin 保留全部字段
        return ret


class ChangePasswordSerializer(serializers.Serializer):
    """修改密码序列化器"""

    old_password = serializers.CharField(
        required=True, write_only=True,
        label='旧密码',
    )
    new_password = serializers.CharField(
        required=True, write_only=True,
        validators=[validate_password],
        label='新密码',
    )

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('旧密码不正确')
        return value
