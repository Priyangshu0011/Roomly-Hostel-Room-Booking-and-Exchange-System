from django.urls import path

from . import api

app_name = 'swaps_api'

urlpatterns = [
    path('exchange-requests/', api.exchange_requests_api, name='exchange_requests'),
    path('proposals/', api.proposals_api, name='proposals'),
    path('proposals/<int:proposal_id>/respond/', api.proposal_respond_api, name='respond'),
    path('proposals/<int:proposal_id>/approve/', api.proposal_approve_api, name='approve'),
    path('transfers/', api.transfers_api, name='transfers'),
    path('transfers/<uuid:transfer_id>/', api.transfer_detail_api, name='transfer_detail'),
]
