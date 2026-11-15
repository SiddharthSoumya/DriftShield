from django.conf import settings


def deployment_mode(request):
    return {"ebs_pilot_mode": settings.DS_PILOT_EBS}
