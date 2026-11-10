"""成都东软学院 — 学校基础信息公开 API

所有接口无需 Token（AllowAny），面向全校师生与访客。
路由前缀在 config/urls.py 中挂载为 /api/school/
"""
from rest_framework import serializers, status
from rest_framework.generics import (
    ListAPIView, RetrieveAPIView, ListCreateAPIView,
    get_object_or_404,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import (
    SchoolProfile, College, Scholarship, Tuition, AdmissionRule, AcademicCalendar,
)
from apps.classify.models import Discipline


# ===========================================================================
# Serializers
# ===========================================================================

class SchoolProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolProfile
        fields = '__all__'


class CollegeListSerializer(serializers.ModelSerializer):
    major_count = serializers.SerializerMethodField()

    class Meta:
        model = College
        fields = ['id', 'name', 'code', 'intro', 'dean', 'website',
                  'features', 'major_count']

    def get_major_count(self, obj):
        return obj.majors.count()


class CollegeDetailSerializer(serializers.ModelSerializer):
    majors = serializers.SerializerMethodField()

    class Meta:
        model = College
        fields = '__all__'

    def get_majors(self, obj):
        majors = obj.majors.all().order_by('sort_order')
        return MajorBriefSerializer(majors, many=True).data


class MajorBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Discipline
        fields = ['id', 'name', 'category', 'level', 'major_code',
                  'duration', 'is_first_class', 'specialty_type',
                  'admission_batch', 'feature_major']


class MajorDetailSerializer(serializers.ModelSerializer):
    college_name = serializers.CharField(source='college.name', read_only=True, default='')

    class Meta:
        model = Discipline
        fields = '__all__'


class ScholarshipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scholarship
        fields = '__all__'


class TuitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tuition
        fields = '__all__'


class AdmissionRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdmissionRule
        fields = '__all__'


class AcademicCalendarSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicCalendar
        fields = '__all__'


# ===========================================================================
# Views
# ===========================================================================

class SchoolProfileView(APIView):
    """GET /api/school/profile/ — 学校概况（单例）"""
    permission_classes = [AllowAny]

    def get(self, request):
        profile = SchoolProfile.objects.first()
        if not profile:
            return Response({'detail': '学校概况尚未配置'}, status=404)
        return Response(SchoolProfileSerializer(profile).data)


class CollegeListView(ListAPIView):
    """GET /api/school/colleges/ — 学院列表"""
    permission_classes = [AllowAny]
    serializer_class = CollegeListSerializer
    queryset = College.objects.all().order_by('sort_order')


class CollegeDetailView(RetrieveAPIView):
    """GET /api/school/colleges/{id}/ — 学院详情（含下属专业）"""
    permission_classes = [AllowAny]
    serializer_class = CollegeDetailSerializer
    queryset = College.objects.all()


class MajorListView(ListAPIView):
    """GET /api/school/majors/ — 专业列表
    Query params:
      - college_id: int  按学院筛选
      - category:  str  按学科门类筛选（工学/管理学/...）
      - level:     str  按培养层次筛选（bachelor/vocational/...）
    """
    permission_classes = [AllowAny]
    serializer_class = MajorBriefSerializer

    def get_queryset(self):
        qs = Discipline.objects.all().order_by('college_id', 'sort_order')
        cid = self.request.query_params.get('college_id')
        if cid:
            qs = qs.filter(college_id=int(cid))
        cat = self.request.query_params.get('category')
        if cat:
            qs = qs.filter(category=cat)
        level = self.request.query_params.get('level')
        if level:
            qs = qs.filter(level=level)
        return qs


class MajorDetailView(RetrieveAPIView):
    """GET /api/school/majors/{id}/ — 专业详情"""
    permission_classes = [AllowAny]
    serializer_class = MajorDetailSerializer
    queryset = Discipline.objects.all()


class ScholarshipListView(ListAPIView):
    """GET /api/school/scholarships/ — 奖助学金列表"""
    permission_classes = [AllowAny]
    serializer_class = ScholarshipSerializer
    queryset = Scholarship.objects.all().order_by('sort_order')


class TuitionListView(ListAPIView):
    """GET /api/school/tuitions/ — 学费标准列表"""
    permission_classes = [AllowAny]
    serializer_class = TuitionSerializer
    queryset = Tuition.objects.all()


class AdmissionRuleLatestView(APIView):
    """GET /api/school/admission/ — 最新年份录取规则"""
    permission_classes = [AllowAny]

    def get(self, request):
        obj = AdmissionRule.objects.order_by('-year').first()
        if not obj:
            return Response({'detail': '暂无录取规则数据'}, status=404)
        return Response(AdmissionRuleSerializer(obj).data)


class AdmissionRuleYearView(RetrieveAPIView):
    """GET /api/school/admission/{year}/ — 指定年份录取规则"""
    permission_classes = [AllowAny]
    serializer_class = AdmissionRuleSerializer

    def get_object(self):
        return get_object_or_404(AdmissionRule, year=int(self.kwargs['year']))


class AcademicCalendarListView(ListAPIView):
    """GET /api/school/calendar/ — 学期/校历列表"""
    permission_classes = [AllowAny]
    serializer_class = AcademicCalendarSerializer
    queryset = AcademicCalendar.objects.all().order_by('sort_order')
