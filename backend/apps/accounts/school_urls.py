"""成都东软学院 — 学校基础信息公开 API 路由"""
from django.urls import path
from .school_views import (
    AcademicCalendarListView,
    AdmissionRuleLatestView,
    AdmissionRuleYearView,
    CollegeDetailView,
    CollegeListView,
    MajorDetailView,
    MajorListView,
    ScholarshipListView,
    SchoolProfileView,
    TuitionListView,
)

urlpatterns = [
    # 学校概况（单例）
    path('profile/', SchoolProfileView.as_view(), name='school_profile'),

    # 学院
    path('colleges/', CollegeListView.as_view(), name='college_list'),
    path('colleges/<int:pk>/', CollegeDetailView.as_view(), name='college_detail'),

    # 专业
    path('majors/', MajorListView.as_view(), name='major_list'),
    path('majors/<int:pk>/', MajorDetailView.as_view(), name='major_detail'),

    # 奖助学金
    path('scholarships/', ScholarshipListView.as_view(), name='scholarship_list'),

    # 学费标准
    path('tuitions/', TuitionListView.as_view(), name='tuition_list'),

    # 录取规则
    path('admission/', AdmissionRuleLatestView.as_view(), name='admission_latest'),
    path('admission/<int:year>/', AdmissionRuleYearView.as_view(), name='admission_year'),

    # 校历
    path('calendar/', AcademicCalendarListView.as_view(), name='academic_calendar'),
]
