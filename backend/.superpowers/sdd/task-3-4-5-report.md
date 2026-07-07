# Task 3-4-5 Report: FAQ Serializers, Views, URL Routing

## Status: DONE

## Files Created
- `backend/apps/faq/serializers.py` — 5 serializers: FAQCategorySerializer, FAQItemListSerializer, FAQItemDetailSerializer, FAQItemDraftSerializer, FAQItemPublishSerializer
- `backend/apps/faq/views.py` — 8 views: FAQCategoryListView, FAQItemListView, FAQItemDetailView, FAQDraftListView, FAQDraftDetailView, FAQDraftActionView, FAQManageListView, FAQManageDeleteView
- `backend/apps/faq/urls.py` — 9 URL patterns covering public endpoints (categories, items, item detail) and admin endpoints (drafts, draft detail, draft action, manage items, manage item delete)

## Design Decisions / Fixes
- Fixed operator precedence bug in `FAQItemListView.get_queryset()` and `FAQItemDetailView.get_queryset()`: replaced `college__isnull=True | Q(...)` (which would raise `TypeError`) with `Q(college__isnull=True) | Q(college_id=...)` using proper `Q` object composition, matching the pattern already used in the admin views.

## Commits
```
n/a (not yet committed)
```

## Test Results
- `python -c "import ... "` — Serializers and Views OK (import verification passed)

## Concerns
- The FAQ URLs need to be registered in `config/urls.py` via `path('api/', include('apps.faq.urls'))` in Task 8.
- The `IsSuperAdmin` import in views.py is currently unused (only `IsFAQAdmin` is referenced across all views); kept for spec compliance.
