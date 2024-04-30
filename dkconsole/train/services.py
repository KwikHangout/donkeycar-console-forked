import json
import logging
import os, shutil
import subprocess, multiprocessing
import uuid
from pathlib import Path

import requests
from django.conf import settings
from requests_toolbelt.multipart.encoder import MultipartEncoder
from rest_framework import status
from django.core.files.storage import FileSystemStorage

# DI
from dkconsole.service_factory import factory
from dkconsole.vehicle.vehicle_service import VehicleService
from .models import Job, JobStatus

vehicle_service: VehicleService = factory.create('vehicle_service')

logger = logging.getLogger(__name__)


class TrainService():
    refresh_lock = False
    MODEL_DIR = settings.MODEL_DIR
    MOVIE_DIR = settings.MOVIE_DIR
    DATA_DIR  = settings.DATA_DIR
    REFRESH_JOB_STATUS_URL = f'{settings.HQ_BASE_URL}/train/refresh_job_statuses'
    SUBMIT_JOB_URL = f'{settings.HQ_BASE_URL}/train/submit_job_handler'

    vehicle_service = factory.create('vehicle_service')
    tub_service = factory.create('tub_service')

    @classmethod
    def get_jobs(cls):
        try:
            cls.refresh_all_job_status()
        except Exception as e:
            print(e)

        jobs = Job.objects.all()
        return jobs

    @classmethod
    def create_job(cls, tub_paths):
        tub_paths_str = ",".join(tub_paths)
        job = Job(tub_paths=tub_paths_str)
        job.status = JobStatus.SCHEDULED
        job.save()

        return job

    @classmethod
    def get_tub_uuid(cls, tub_path):
        meta_json_path = cls.tub_service.get_meta_json_path(Path(tub_path))
        with open(meta_json_path) as f:
            meta = json.load(f)
        return meta['uuid']

    @classmethod
    def submit_job(cls, tub_paths, id_token=None, newjob):
        job = newjob
        if not newjob:
          job = cls.create_job(tub_paths)

        job_id = job.uuid
        if not job_id:
              job_id=""

        filename = cls.tub_service.generate_tub_archive(tub_paths)

        mp_encoder = MultipartEncoder(
            fields={
                'device_id': cls.vehicle_service.get_wlan_mac_address() or "Null",
                'hostname': cls.vehicle_service.get_hostname(),
                'tub_archive_file': ('file.tar.gz', open(filename, 'rb'), 'application/gzip'),
                'donkeycar_version': str(vehicle_service.get_donkeycar_version()
                'job_id':job_id)
            }
        )
        print(cls.SUBMIT_JOB_URL)
        logger.debug("Posting job to HQ", cls.SUBMIT_JOB_URL)

        try:

          r = requests.post(
              cls.SUBMIT_JOB_URL,
              data=mp_encoder,  # The MultipartEncoder is posted as data, don't use files=...!
              # The MultipartEncoder provides the content-type header with the boundary:
              headers={'Content-Type': mp_encoder.content_type, 'Authorization': id_token if id_token else ''},
          )

          if r.status_code == status.HTTP_200_OK:
              if "job_uuid" in r.json():
                  try:
                      print(r.json()['job_uuid'])
                      uuid.UUID(r.json()['job_uuid'], version=4)
                      job.uuid = r.json()['job_uuid']
                      job.save()
                  except Exception as e:
                      print(e)
                      raise Exception("Failed to call submit job")
              else:
                  raise Exception("Failed to call submit job")
          else:
              raise Exception(f"Failed to call submit job, ERROR {r.status_code}")
        except Exception as e:
            logger.error(e)
            raise Exception("Failed to call submit job")

    @classmethod
    def submit_job_v2(cls, tub_paths, id_token=None):
        job = cls.create_job(tub_paths)
        tub_uuids = list(map(cls.get_tub_uuid, tub_paths))
        filename = f"{settings.CARAPP_PATH}/myconfig.py"

        try:
            data = [
                ('myconfig_file', ('myconfig.py', open(filename, 'rb'), 'text/plain')),
                ('device_id', cls.vehicle_service.get_wlan_mac_address()),
                ('hostname', cls.vehicle_service.get_hostname()),
                ('donkeycar_version', str(vehicle_service.get_donkeycar_version())),
            ]
            for _ in tub_uuids:
                data.append(('tub_uuids', _))

            mp_encoder = MultipartEncoder(
                fields=data
            )

            logger.debug("Posting job to HQ with submit job v2")
            r = requests.post(
                cls.SUBMIT_JOB_URL,
                data=mp_encoder,  # The MultipartEncoder is posted as data, don't use files=...!
                # The MultipartEncoder provides the content-type header with the boundary:
                headers={'Content-Type': mp_encoder.content_type, 'Authorization': id_token if id_token else ''}
            )

            if r.status_code == status.HTTP_200_OK:
                if "job_uuid" in r.json():
                    uuid.UUID(r.json()['job_uuid'], version=4)
                    job.uuid = r.json()['job_uuid']
                    job.save()

                    cls.submit_job(tub_paths, id_token=id_token, job.uuid)

                else:
                    raise Exception("Failed to call submit job v2")
            else:
                raise Exception("Failed to call submit job v2")
        except Exception as e:
            logger.error(e)
            raise Exception("Failed to call submit job v2")

    # @classmethod
    # def refresh_all_job_status(cls):
    #     jobs = Job.objects.filter(status__in = JobStatus.OS_STATUSES)
    #     job_uuids = [job.uuid for job in jobs]
    #     cls.refresh_job_status(job_uuids)
    #     return len(jobs)

    @classmethod
    def refresh_all_job_status(cls):
        if cls.refresh_lock is False:
            try:
                cls.refresh_lock = True  # lock this function to prevent other thread calling the same time. E.g. mobile app user pull-to-refresh twice accidentally
                jobs = Job.objects.filter(status__in=JobStatus.OS_STATUSES)
                print([job.uuid for job in jobs])
                if len(jobs) > 0:
                    job_uuids = [str(job.uuid) for job in jobs if job.uuid is not None]
                    updated_jobs = cls.get_latest_job_status_from_hq(job_uuids)

                    for result in updated_jobs:
                        if ("uuid" in result):
                            job = Job.objects.get(uuid=result['uuid'])
                            for j in Job.objects.all():
                                print(str(j.uuid) + ":"+ j.status)
                            job.status = result['status']
                            print("result[status]:" + job.status)
                            job.model_url = result['model_url']
                            job.model_accuracy_url = result['model_accuracy_url']
                            job.model_movie_url = result['model_movie_url']
                            job.save()

                            # Background download h5, model accuracy url and etc
                            if job.status == JobStatus.COMPLETED:
                                cls.download_model(job)

            finally:
                cls.refresh_lock = False

    @classmethod
    def download_model(cls, job):
        print("download_model:"+cls.MODEL_DIR)
        if vehicle_service.get_donkeycar_version().major == 4:
            print("download .h5")
            cls.download_file(job.model_url, f"{cls.MODEL_DIR}/job_{job.id}.h5")
        elif vehicle_service.get_donkeycar_version().major == 5:
            print("download and unzip")
            def download_and_unzip_savedmodel(url, filepath, jobid):
                print(url)
                os.system(" ".join(["curl", "--fail", url, "--output", filepath]))
                if os.path.exists(f"{cls.MODEL_DIR}/.job_{jobid}.savedmodel.temp"):
                    shutil.rmtree(f"{cls.MODEL_DIR}/.job_{jobid}.savedmodel.temp")
                os.mkdir(f"{cls.MODEL_DIR}/.job_{jobid}.savedmodel.temp")
                os.system(f"tar -xzf {filepath} -C {os.path.dirname(filepath)}/.job_{jobid}.savedmodel.temp -k")
                os.remove(filepath)
                os.rename(f"{cls.MODEL_DIR}/.job_{jobid}.savedmodel.temp/model.savedmodel",
                           f"{cls.MODEL_DIR}/job_{jobid}.savedmodel")
                shutil.rmtree(f"{cls.MODEL_DIR}/.job_{jobid}.savedmodel.temp")
            multiprocessing.Process(target=download_and_unzip_savedmodel, args=(job.model_url, f"{cls.MODEL_DIR}/job_{job.id}.tar.gz", job.id)).start()
        cls.download_file(job.model_url.rstrip('h5')+"tflite", f"{cls.MODEL_DIR}/job_{job.id}.tflite")
        cls.download_file(job.model_accuracy_url, f"{cls.MODEL_DIR}/job_{job.id}.png")
        # cls.download_file(job.model_myconfig_url, f"{cls.MODEL_DIR}/job_{job.id}.myconfig.py")

        if not os.path.isdir(cls.MOVIE_DIR):
            logger.info(f"Creating movie folder {cls.MOVIE_DIR}")
            os.mkdir(cls.MOVIE_DIR)

        cls.download_file(job.model_movie_url, f"{cls.MOVIE_DIR}/job_{job.id}.mp4")
        # cls.download_file()

    @classmethod
    def download_file(cls, url, target_path):
        logger.debug(f"Downloading file from {url} to {target_path}")
        command = ["curl", "--fail", url, "--output", target_path]
        proc = subprocess.Popen(command)

    @classmethod
    def get_latest_job_status_from_hq(cls, job_uuids, id_token=None):
        print(f"Getting lastest job status for uuid {job_uuids}")
        # job_uuids = [job_uuid for job_uuid in job_uuids if job_uuid]
        response = requests.post(cls.REFRESH_JOB_STATUS_URL, data={"job_uuids": job_uuids},
                                 headers={'Authorization': id_token if id_token else ''})
        if response.status_code == status.HTTP_200_OK:
            return response.json()
        else:
            print(response.status_code)
            print(response.content)
            raise Exception("Problem requesting latest job status from hq")

    @classmethod
    def delete_jobs(cls, job_ids):
        for id in job_ids:
            jobWillDelete = Job.objects.get(id=id)
            jobWillDelete.delete()

    @classmethod
    def get_model_movie_path(cls, job_id):
        job = Job.objects.get(id=job_id)

        model_movie_path = settings.MOVIE_DIR + f"/job_{job.id}.mp4"

        if os.path.isfile(model_movie_path):
            return model_movie_path
        else:
            return None

    @classmethod
    def save_for_training(cls, myfile, device_id, hostname, job_id):
        fs = FileSystemStorage()
        filename = fs.save(myfile.name, myfile)
        uploaded_file_url = fs.url(filename)
        print(uploaded_file_url)

        statusfilename = fs.save(job_id+".json", {"device_id":device_id, "hostname":hostname, "uuid": job_id, "status": JobStatus.SCHEDULED, "model_url": "some-url", "model_accuracy_url": "some-url", "model_movie_url":"movie-url"})
        print(statusfilename)
        return filename

    @classmethod
    def extract_for_training(cls, filepath, jpbid):
      def extract_data(filepath):
          print(filepath)
          os.system(f"tar -xzf {filepath} -C {cls.DATA_DIR} -k")
      multiprocessing.Process(target=extract_data, args=(filepath)).start()

    # @classmethod
    # def get_train_uuid(cls, device_id, hostname):
    #     return job_uuid

    @classmethod
    def get_train_uuid(cls, job_id):
        if os.path.exists(job_id+".json"):
            with open(job_id+".json", 'rb') as fh:
                status = json.load(fh)
                return [status]
        else:
            return [{"uuid": job_id, "status": JobStatus.SCHEDULED}]

    @classmethod
    def get_statuses(cls, job_id):
        if os.path.exists(job_id+".json"):
            with open(job_id+".json", 'rb') as fh:
                status = json.load(fh)
                return [status]
        else:
            return [{"uuid": job_id, "status": JobStatus.SCHEDULED}]
