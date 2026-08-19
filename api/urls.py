from django.urls import path

from api import auth_views, views

app_name = "api"

urlpatterns = [
    path("health", views.health, name="health"),
    path("auth/phone/start", auth_views.phone_start, name="auth_phone_start"),
    path("auth/phone/verify", auth_views.phone_verify, name="auth_phone_verify"),
    path("auth/logout", auth_views.logout, name="auth_logout"),
    path("devices", auth_views.register_device, name="devices"),
    path("me", views.me, name="me"),
    path("me/access", views.me_access, name="me_access"),
    path("me/subscription", views.me_subscription, name="me_subscription"),
    path("me/receipts", views.me_receipts, name="me_receipts"),
    path("executor-roles", views.executor_roles, name="executor_roles"),
    path("work-requests", views.work_requests_list, name="work_requests"),
    path("work-requests/<int:pk>", views.work_request_detail, name="work_request_detail"),
    path(
        "work-requests/<int:pk>/confirm-amount",
        views.work_request_confirm_amount,
        name="work_request_confirm",
    ),
    path(
        "work-requests/<int:pk>/photos",
        views.work_request_add_photo,
        name="work_request_photos",
    ),
    path(
        "work-requests/<int:pk>/submit",
        views.work_request_submit,
        name="work_request_submit",
    ),
    path("collections", views.collections_list, name="collections"),
    path("collections/<int:pk>", views.collection_detail, name="collection_detail"),
    path("onboarding", views.onboarding, name="onboarding"),
    path(
        "onboarding/steps/<slug:code>/complete",
        views.onboarding_complete_step,
        name="onboarding_step",
    ),
    path("notifications", views.notifications_list, name="notifications"),
    path(
        "notifications/<int:pk>/read",
        views.notification_read,
        name="notification_read",
    ),
]
