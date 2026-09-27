from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from core import views, auth_views
# Detail and action routes are keyed by an opaque UUID (`public_id`). The old
# /clientes/<int>/ style URLs match no pattern any more and therefore 404.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth.LoginView.as_view(), name='login'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('recuperar/', auth.PasswordResetView.as_view(template_name='registration/form.html',html_email_template_name='registration/password_reset_email_html.html'), name='password_reset'),
    path('recuperar/enviado/', auth.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('recuperar/<uidb64>/<token>/', auth.PasswordResetConfirmView.as_view(template_name='registration/form.html'), name='password_reset_confirm'),
    path('recuperar/completo/', auth.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('invitacion/<str:token>/', auth_views.accept_invitation, name='accept_invitation'),
    path('2fa/', auth_views.two_factor, name='two_factor'),
    path('usuarios/', auth_views.users, name='users'),
    path('', views.home, name='home'),
    path('clientes/', views.businesses, name='businesses'),
    path('portal/', views.portal, name='portal'),
]
urlpatterns += [
    path('clientes/nuevo/', views.business_create, name='business_create'),
    path('clientes/<uuid:public_id>/', views.business_detail, name='business_detail'),
    path('clientes/<uuid:public_id>/editar/', views.business_edit, name='business_edit'),
    path('clientes/<uuid:public_id>/recurso/<str:kind>/', views.business_resource, name='business_resource'),
    path('clientes/<uuid:public_id>/portal/', views.portal_preview, name='portal_preview'),
    path('clientes/<uuid:public_id>/peticiones/', views.request_create, name='request_create'),
    path('clientes/<uuid:public_id>/estudio/', views.study, name='study'),
    path('peticiones/<uuid:public_id>/estado/', views.request_status, name='request_status'),
    path('documentos/<uuid:public_id>/', views.document_download, name='document_download'),
    path('portal/negocio/<uuid:public_id>/', views.portal, name='portal_business'),
    path('portal/<str:kind>/', views.portal_list, name='portal_list'),
]
from core import proposal_views
urlpatterns += [
    path('propuestas/',proposal_views.proposal_list,name='proposals'),
    path('propuestas/nueva/',proposal_views.proposal_create,name='proposal_create'),
    path('propuestas/<uuid:public_id>/',proposal_views.proposal_detail,name='proposal_detail'),
    path('propuestas/<uuid:public_id>/pdf/',proposal_views.proposal_detail,{'pdf':True},name='proposal_pdf'),
    path('propuestas/<uuid:public_id>/editar/',proposal_views.proposal_edit,name='proposal_edit'),
    path('propuestas/<uuid:public_id>/accion/<str:action>/',proposal_views.action,name='proposal_action'),
    path('propuesta-publica/<str:token>/',proposal_views.public_proposal,name='public_proposal'),
    path('propuesta-publica/<str:token>/pdf/',proposal_views.public_proposal,{'pdf':True},name='public_proposal_pdf'),
]
from core import pipeline_views
urlpatterns += [
    path('buscar/',pipeline_views.search,name='search'),
    path('buscar/actualizar/',pipeline_views.refresh,name='search_refresh'),
    path('buscar/progreso/<uuid:public_id>/',pipeline_views.search_progress,name='search_progress'),
    path('buscar/<uuid:public_id>/captar/',pipeline_views.place_capture,name='place_capture'),
    path('buscar/<uuid:public_id>/descartar/',pipeline_views.place_discard,name='place_discard'),
    path('clientes/<uuid:public_id>/fase/',pipeline_views.stage,name='stage'),
    path('clientes/<uuid:public_id>/seguimiento/',pipeline_views.followup,name='followup'),
    path('clientes/<uuid:public_id>/visita/',pipeline_views.followup,{'visit':True},name='visit'),
    path('seguimiento/<uuid:public_id>/hecho/',pipeline_views.followup_done,name='followup_done'),
]
from core import finance_views
# Place the specific portal route before the generic listing route.
urlpatterns.insert(0,path('portal/gastos/',finance_views.portal_charges,name='portal_charges'))
urlpatterns += [
    path('finanzas/',finance_views.finance,name='finance'),
    path('finanzas/exportar/',finance_views.export_csv,name='finance_csv'),
    path('finanzas/nuevo/<str:kind>/',finance_views.finance_create,name='finance_create'),
    path('finanzas/justificante/<uuid:public_id>/',finance_views.receipt,name='receipt'),
]
urlpatterns += [path('usuarios/<uuid:public_id>/',auth_views.user_edit,name='user_edit')]
urlpatterns += [path('finanzas/<str:kind>/<uuid:public_id>/editar/',finance_views.finance_edit,name='finance_edit')]
urlpatterns += [path('documentos/<uuid:public_id>/estado/',views.document_edit,name='document_edit')]
urlpatterns += [path('peticiones/<uuid:public_id>/planificar/',views.request_plan,name='request_plan')]
urlpatterns += [path('mas/',views.more,name='more'),path('clientes/<uuid:public_id>/nota/',views.business_note,name='business_note')]

from core import pwa_views
urlpatterns += [
    path('sw.js', pwa_views.service_worker, name='service_worker'),
    path('manifest.webmanifest', pwa_views.manifest, name='manifest'),
    path('offline/', pwa_views.offline, name='offline'),
]
# The demo is the one public page under /clientes/: its view carries no @roles.
urlpatterns += [
    path('clientes/<uuid:public_id>/demo-cliente', views.demo_public, name='demo_public'),
    path('clientes/<uuid:public_id>/demo-cliente/', views.demo_public),
    path('clientes/<uuid:public_id>/demo/', views.demo_upload, name='demo_upload'),
    path('clientes/<uuid:public_id>/demo/eliminar/', views.demo_delete, name='demo_delete'),
]
