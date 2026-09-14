from django.http import HttpResponse


def webhook(request):
    return HttpResponse("em construção", status=501)
