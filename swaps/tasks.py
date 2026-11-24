from celery import shared_task

from .services import create_matching_proposals, expire_exchange_workflows


@shared_task
def process_exchange_workflows():
    expired_requests = expire_exchange_workflows()
    proposals = create_matching_proposals()
    return {
        'expired_requests': expired_requests,
        'proposals_created': len(proposals),
    }
