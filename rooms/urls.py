from django.urls import path
from . import views

app_name = 'rooms'

urlpatterns = [
    path('', views.room_list_view, name='room_list'),
    path('<int:room_id>/', views.room_detail_view, name='room_detail'),
    path('operations/', views.operations_view, name='operations'),
]
