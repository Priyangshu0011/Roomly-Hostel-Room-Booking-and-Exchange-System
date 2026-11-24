from django.contrib import admin

from .models import (
    Approval,
    AuditEntry,
    ExchangeProposal,
    ExchangeRequest,
    PolicyRule,
    ProposalParticipant,
    TransferRecord,
)


@admin.register(ExchangeRequest)
class ExchangeRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'requester', 'from_room', 'status', 'created_at', 'expires_at')
    list_filter = ('status', 'created_at', 'from_room__block')
    search_fields = ('requester__user__username', 'from_room__room_number')
    readonly_fields = ('requester', 'from_room', 'reason', 'created_at', 'updated_at', 'expires_at')

    def has_module_permission(self, request):
        return request.user.has_perm('swaps.view_exchangerequest')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('swaps.view_exchangerequest')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ProposalParticipantInline(admin.TabularInline):
    model = ProposalParticipant
    extra = 0
    can_delete = False
    readonly_fields = ('exchange_request', 'student', 'from_room', 'to_room', 'response', 'responded_at')


@admin.register(ExchangeProposal)
class ExchangeProposalAdmin(admin.ModelAdmin):
    list_display = ('id', 'cycle_length', 'status', 'created_at', 'expires_at')
    list_filter = ('status', 'cycle_length', 'created_at')
    search_fields = ('cycle_key',)
    readonly_fields = ('cycle_length', 'status', 'expires_at', 'created_at', 'resolved_at', 'created_by', 'cycle_key')
    inlines = (ProposalParticipantInline,)

    def has_module_permission(self, request):
        return request.user.has_perm('swaps.view_exchangeproposal')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('swaps.view_exchangeproposal')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PolicyRule)
class PolicyRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'cooling_off_days', 'request_validity_days', 'proposal_validity_hours', 'max_chain_length', 'max_preferences', 'updated_at')
    readonly_fields = ('updated_at', 'updated_by')

    def has_module_permission(self, request):
        return request.user.has_perm('swaps.manage_policyrule')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('swaps.view_policyrule')

    def has_add_permission(self, request):
        return request.user.has_perm('swaps.add_policyrule')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('swaps.change_policyrule')

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('swaps.delete_policyrule')

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        AuditEntry.objects.create(
            actor=request.user,
            action='policy.updated',
            object_type='PolicyRule',
            object_id=str(obj.pk),
            details={
                'cooling_off_days': obj.cooling_off_days,
                'request_validity_days': obj.request_validity_days,
                'proposal_validity_hours': obj.proposal_validity_hours,
                'max_chain_length': obj.max_chain_length,
            },
        )


@admin.register(Approval)
class ApprovalAdmin(admin.ModelAdmin):
    list_display = ('proposal', 'decision', 'approver', 'decided_at')
    list_filter = ('decision', 'decided_at')
    readonly_fields = ('proposal', 'approver', 'decision', 'remarks', 'decided_at')

    def has_module_permission(self, request):
        return request.user.has_perm('swaps.view_approval')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('swaps.view_approval')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TransferRecord)
class TransferRecordAdmin(admin.ModelAdmin):
    list_display = ('transfer_id', 'proposal', 'completed_at')
    list_filter = ('completed_at',)
    readonly_fields = ('transfer_id', 'proposal', 'completed_at', 'participant_snapshot', 'approval_remarks')

    def has_module_permission(self, request):
        return request.user.has_perm('swaps.view_transferrecord')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('swaps.view_transferrecord')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'object_type', 'object_id')
    list_filter = ('action', 'object_type', 'created_at')
    search_fields = ('actor__username', 'action', 'object_type', 'object_id')
    readonly_fields = ('actor', 'action', 'object_type', 'object_id', 'details', 'created_at')
    ordering = ('-created_at',)

    def has_module_permission(self, request):
        return request.user.has_perm('swaps.view_auditentry')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('swaps.view_auditentry')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
