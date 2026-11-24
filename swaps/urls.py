from django.urls import path

from . import views

app_name = 'swaps'

urlpatterns = [
    path('requests/', views.my_exchange_requests_view, name='list'),
    path('requests/create/', views.create_exchange_request_view, name='create'),
    path('requests/<int:request_id>/', views.exchange_request_detail_view, name='request_detail'),
    path('requests/<int:request_id>/edit/', views.edit_exchange_request_view, name='edit'),
    path('requests/<int:request_id>/withdraw/', views.withdraw_exchange_request_view, name='withdraw'),
    path('proposals/', views.proposal_list_view, name='proposals'),
    path('proposals/<int:proposal_id>/respond/', views.respond_to_proposal_view, name='respond'),
    path('approval-queue/', views.approval_queue_view, name='approval_queue'),
    path('proposals/<int:proposal_id>/approve/', views.approve_proposal_view, name='approve'),
    path('transfers/', views.transfer_list_view, name='transfers'),
    path('transfers/<uuid:transfer_id>/', views.transfer_detail_view, name='transfer_detail'),
    path('reports/', views.analytics_view, name='analytics'),
]
