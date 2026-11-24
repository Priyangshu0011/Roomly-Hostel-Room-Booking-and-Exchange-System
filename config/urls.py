from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import RedirectView


def health_check(request):
    return JsonResponse({'status': 'ok'})


def handler404_view(request, exception=None):
    return render(request, '404.html', status=404)


def handler500_view(request):
    return render(request, '500.html', status=500)


handler404 = handler404_view
handler500 = handler500_view

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='rooms:room_list', permanent=False), name='home'),
    path('healthz/', health_check, name='health_check'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls', namespace='accounts')),
    path('rooms/', include('rooms.urls', namespace='rooms')),
    path('swaps/', include('swaps.urls', namespace='swaps')),
    path('', include('swaps.api_urls', namespace='swaps_api')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
