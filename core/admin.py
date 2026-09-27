from django.apps import apps
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(pk__in=User.objects.visible_to(request.user).values('pk'))
    ordering = ['email']
    list_display = ['email', 'name', 'role', 'is_active']
    fieldsets = ((None, {'fields': ('email', 'password', 'name', 'role', 'is_active')}),)
    add_fieldsets = ((None, {'fields': ('email', 'name', 'role', 'password1', 'password2')}),)
    search_fields = ['email', 'name']
    def has_module_permission(self, request): return request.user.role=='admin'
    def has_view_permission(self, request, obj=None): return request.user.role=='admin'
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

class RestrictedAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).visible_to(request.user)
    def has_module_permission(self, request):
        return request.user.role == 'admin'
    def has_view_permission(self, request, obj=None):
        return request.user.role == 'admin'
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

for model in apps.get_app_config('core').get_models():
    if model != User:
        admin.site.register(model, RestrictedAdmin)

from .models import SearchZone,SearchCategory,Assignment,ClientMembership
class ConfigurationAdmin(RestrictedAdmin):
    def has_add_permission(self,request): return request.user.role=='admin'
    def has_change_permission(self,request,obj=None): return request.user.role=='admin'
    def has_delete_permission(self,request,obj=None): return request.user.role=='admin'
for model in [SearchZone,SearchCategory,Assignment,ClientMembership]:
    admin.site.unregister(model)
    admin.site.register(model,ConfigurationAdmin)
