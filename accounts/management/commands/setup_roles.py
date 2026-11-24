from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from accounts.models import StudentProfile
from rooms.models import HostelBlock, Room
from swaps.models import (
    Approval,
    AuditEntry,
    ExchangeProposal,
    ExchangeRequest,
    PolicyRule,
    ProposalParticipant,
    TransferRecord,
)
from swaps.permissions import (
    CHIEF_WARDEN_GROUP,
    DEAN_GROUP,
    HOSTEL_ADMIN_GROUP,
    STUDENT_GROUP,
    WARDEN_GROUP,
)


ROLE_MODELS = {
    STUDENT_GROUP: (),
    WARDEN_GROUP: (),
    CHIEF_WARDEN_GROUP: (),
    HOSTEL_ADMIN_GROUP: (),
    DEAN_GROUP: (),
}


def model_permissions(model, actions):
    content_type = ContentType.objects.get_for_model(model)
    permission_names = [f'{action}_{model._meta.model_name}' for action in actions]
    return list(Permission.objects.filter(
        content_type=content_type,
        codename__in=permission_names,
    ))


def custom_permission(model, codename):
    content_type = ContentType.objects.get_for_model(model)
    return Permission.objects.filter(content_type=content_type, codename=codename)


class Command(BaseCommand):
    help = 'Create the standard P04 role groups and grant their minimum permissions.'

    def handle(self, *args, **options):
        groups = {
            name: Group.objects.get_or_create(name=name)[0]
            for name in ROLE_MODELS
        }

        warden_permissions = list(custom_permission(ExchangeProposal, 'review_exchangeproposal'))
        groups[WARDEN_GROUP].permissions.set(warden_permissions)
        groups[CHIEF_WARDEN_GROUP].permissions.set(warden_permissions)

        administrator_permissions = (
            model_permissions(HostelBlock, ('view', 'add', 'change', 'delete'))
            + list(custom_permission(HostelBlock, 'manage_room_inventory'))
            + model_permissions(Room, ('view', 'add', 'change', 'delete'))
            + model_permissions(StudentProfile, ('view', 'change'))
            + list(custom_permission(StudentProfile, 'manage_student_eligibility'))
            + model_permissions(PolicyRule, ('view', 'add', 'change', 'delete'))
            + list(custom_permission(PolicyRule, 'manage_policyrule'))
            + model_permissions(ExchangeRequest, ('view',))
            + model_permissions(ExchangeProposal, ('view',))
            + model_permissions(ProposalParticipant, ('view',))
            + model_permissions(Approval, ('view',))
            + model_permissions(TransferRecord, ('view',))
            + model_permissions(AuditEntry, ('view',))
            + list(custom_permission(AuditEntry, 'view_exchangeanalytics'))
        )
        groups[HOSTEL_ADMIN_GROUP].permissions.set(administrator_permissions)

        dean_permissions = list(custom_permission(AuditEntry, 'view_exchangeanalytics'))
        groups[DEAN_GROUP].permissions.set(dean_permissions)

        for group_name in groups:
            self.stdout.write(self.style.SUCCESS(f'Configured role: {group_name}'))
