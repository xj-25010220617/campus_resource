from rest_framework import generics, permissions
from .models import User
from .serializers import UserSerializer


class MeView(generics.RetrieveAPIView):
    """当前登录用户信息"""
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user
