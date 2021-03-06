from __future__ import absolute_import

import time
import json
import re
import logging

from celery import shared_task
from celery.schedules import crontab
from celery.decorators import periodic_task
from datetime import date, timedelta, datetime

from celery.utils.log import get_task_logger

from smtplib import SMTPException
from workspace.models import WorkspaceHistory
from galaxy.decorator import galaxy_connection
from django.core.mail import send_mail
from django.db import transaction
from django.core.urlresolvers import reverse

def flush_transaction():
    transaction.commit()
    
logger = get_task_logger(__name__)

@shared_task
def monitorworkspace(historyid):
    """
    Celery task that will monitor galaxy workspace

    It will update content of the workspace django model every 10 seconds

    It will wait for end of execution of all jobs
    and send a mail at the end, if the mail has been
    given by the user.
    
    this task is launched by workspace.views.HistoryDetailView
    only once, when the WorkspaceHistory is marked as "notmonitored"
    """
    galaxycon = galaxy_connection()
    galaxycon.nocache = True
    #print "Monitoring workspace " + historyid
    finished = False
    error = False
    email = None

    while not finished:
        try:
            hc = galaxycon.histories.show_history(historyid, contents=True)
            hi = galaxycon.histories.show_history(historyid)
            w = WorkspaceHistory.objects.get(history=historyid)
            w.history_content_json = json.dumps(hc)
            w.history_info_json =  json.dumps(hi)
            w.save()

            finished = True
            for file in hc:
                if ( 'running' in file.get('state','') or
                     'queued' in file.get('state','') or
                     'new' in file.get('state','')):
                    finished = False
                if 'error' in file.get('state',''):
                    error = True
                    finished = True
        except:
            logging.warning('Problem with Galaxy server, waiting 1 minute')
            time.sleep(60)
        
        if not finished:
            time.sleep(10)
            
    w.finished = True
    w.save()
    logging.warning("history finished? %r" % (w.finished))

    
    if w and w.email and re.match(r"[^@]+@[^@]+\.[^@]+", w.email):
        try:
            message = "Dear NGPhylogeny user, \n\n"
            if error:
                message= message + "Your NGPhylogeny job finished with errors.\n\n"
            else:
                message=message + "Your NGPhylogeny job finished successfuly.\n"
            please = 'Please visit http://%s%s to check results\n\n' % ("ngphylogeny.fr", reverse('history_detail', kwargs={'history_id':historyid}))
            message = message + please
            message = message + "Thank you for using ngphylogeny.fr\n\n"
            message = message + "NGPhylogeny.fr development team.\n"
            send_mail(
                'NGPhylogeny.fr results',
                message,
                'ngphylogeny@pasteur.fr',
                [w.email],
                fail_silently=False,
            )
            print(message)
        except SMTPException as e:
            logging.warning("Problem with smtp server : %s" % (e))
        except Exception as e:
            logging.warning("Unknown Problem while sending e-mail: %s" % (e))

@shared_task
def deletegalaxyhistory(historyid):
    """
    Celery task that will delete an history on the galaxy server in background
    """
    logging.info("Deleting history %s" % (historyid))
    try:
        galaxycon = galaxy_connection()
        galaxycon.nocache = True
        galaxycon.histories.delete_history(historyid, purge=True)
    except Exception as e:
        logging.warning("Problem while deleting history: %s" % (e))

# Every day at 2am, clears analyses older than 14 days
@periodic_task(run_every=(crontab(hour="02", minute="00", day_of_week="*")))
def deleteoldgalaxyhistory():
    logger.info("Start old workspace deletion task")
    datecutoff = datetime.now() - timedelta(days=14)
    for e in WorkspaceHistory.objects.filter(deleted=False).filter(finished=True).filter(created_date__lte=datecutoff):
        e.deleted = True
        e.save()
        deletegalaxyhistory(e.history)
    logger.info("Old workspace deletion task finished")
