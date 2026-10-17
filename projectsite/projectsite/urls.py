from django.contrib import admin
from django.urls import path

from weather import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.weather_view, name='weather'),
]
