import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


from dkconsole.train.models import Job
from dkconsole.util import *
from .serializers import JobSerializer
from .serializers import SubmitJobSerializer
from .services import TrainService
from .models import Job, JobStatus

logger = logging.getLogger(__name__)

from uuid import uuid4

# Create your views here.

@api_view(['GET'])
def index(request):
    jobs = TrainService.get_jobs()

    serializer = JobSerializer(jobs, many=True)
    return Response(serializer.data)


@api_view(['POST'])
def submit_job(request):
    try:
        print(request.data)
        serializer = SubmitJobSerializer(data=request.data)

        if serializer.is_valid():
            tub_paths = request.data['tub_paths']
            try:
                id_token = request.data['id_token']
            except KeyError:
                id_token = None

            try:
                if not request.data['v2']:
                    TrainService.submit_job(tub_paths, id_token=id_token)
                else:
                    TrainService.submit_job_v2(tub_paths, id_token=id_token)
            except KeyError:
                TrainService.submit_job(tub_paths)

            return Response({"success": True})
        else:
            return Response(status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(e)
        return Response(status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
def submit_job_handler(request):
    try:
        print("--- submit_job_handler -----")
        print(request.data)
        myconfig_file = request.data.get('myconfig_file')
        tub_archive_file = request.data.get('tub_archive_file')
        device_id = request.data['device_id']
        hostname = request.data['hostname']

        if myconfig_file:
          job_uuid = str(uuid4())
          myfile = request.FILES['myconfig_file']
          TrainService.save_for_training(myfile, device_id, hostname, job_uuid)
        elif tub_archive_file:
          job_uuid = request.data['job_id']
          # TrainService.get_train_uuid(device_id, hostname)
          myfile = request.FILES['tub_archive_file']
          myfile = TrainService.save_for_training(myfile)
          TrainService.extract_for_training(myfile, device_id, hostname)

        return Response({"success": True, "job_uuid":job_uuid})

    except Exception as e:
        logger.error(e)
        return Response(status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
def refresh_job_statuses(request):
    global dummy_counter
    try:
        print(request.data)
        job_id = request.data['job_id']
        return_value = TrainService.get_statuses(job_id)
        return Response(return_value)
    except Exception as e:
        logger.error(e)
        return Response(status=status.HTTP_400_BAD_REQUEST)



@api_view(['POST'])
def refresh_job_status(request):
    '''
    This is supposed to be called by scheduled job per minute
    '''
    count = TrainService.refresh_all_job_status()

    return Response({"count": count})


@api_view(['POST'])
def download_model(request):
    print(request.data)
    job_id = request.data['job_id']
    job = Job.objects.get(pk=job_id)
    TrainService.download_model(job)
    return Response({"success": False})


@api_view(['POST'])
def delete_jobs(request):
    job_ids = request.data['job_ids']
    TrainService.delete_jobs(job_ids)
    return Response({"success": True})


def stream_video(request, job_id):
    path = (TrainService.get_model_movie_path(job_id))

    resp = video_stream(request, path)
    return resp
