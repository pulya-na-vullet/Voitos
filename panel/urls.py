from django.urls import path

from panel import views

app_name = "panel"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.users_list, name="users"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("users/<int:user_id>/", views.user_dashboard, name="user_dashboard"),
    path("users/<int:user_id>/messages/", views.user_messages, name="user_messages"),
    path("users/<int:user_id>/memories/", views.user_memories, name="user_memories"),
    path("users/<int:user_id>/tasks/", views.user_tasks, name="user_tasks"),
    path("users/<int:user_id>/reminders/", views.user_reminders, name="user_reminders"),
    path("users/<int:user_id>/logs/", views.user_logs, name="user_logs"),
    path("users/<int:user_id>/receipts/", views.user_receipts, name="user_receipts"),
    path("receipts/", views.receipts_list, name="receipts"),
    path("receipts/<int:pk>/approve/", views.receipt_approve, name="receipt_approve"),
    path("receipts/<int:pk>/reject/", views.receipt_reject, name="receipt_reject"),
    path("settings/", views.settings_view, name="settings"),
    path("check-max/", views.check_max, name="check_max"),
    path("delete/memory/<int:pk>/", views.delete_memory, name="delete_memory"),
    path("delete/task/<int:pk>/", views.delete_task, name="delete_task"),
    path("delete/reminder/<int:pk>/", views.delete_reminder, name="delete_reminder"),
    path("delete/message/<int:pk>/", views.delete_message, name="delete_message"),
    path("dump/", views.dump_now, name="dump_now"),
]
