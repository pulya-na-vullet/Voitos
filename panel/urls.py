from django.urls import path

from panel import views

app_name = "panel"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("messages/", views.messages_view, name="messages"),
    path("memories/", views.memories_view, name="memories"),
    path("tasks/", views.tasks_view, name="tasks"),
    path("reminders/", views.reminders_view, name="reminders"),
    path("logs/", views.logs_view, name="logs"),
    path("settings/", views.settings_view, name="settings"),
    path("api/activity/", views.activity_api, name="activity_api"),
    path("delete/memory/<int:pk>/", views.delete_memory, name="delete_memory"),
    path("delete/task/<int:pk>/", views.delete_task, name="delete_task"),
    path("delete/reminder/<int:pk>/", views.delete_reminder, name="delete_reminder"),
    path("delete/message/<int:pk>/", views.delete_message, name="delete_message"),
    path("dump/", views.dump_now, name="dump_now"),
]
