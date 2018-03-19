from __future__ import absolute_import, unicode_literals

from django.shortcuts import render
from .forms import UserQueryForm, ScraperForm, SkillsForm
from .models import ScraperParams, QueryLoc
from django.http import HttpResponse

from djangosite.celery import app
from django_celery_results.models import TaskResult
from celery.result import AsyncResult
from .tasks import scrape_dice, get_stackoverflow_skills
from .text_proc import get_word_count, db_text_search
from .utils import get_display_results
import pymongo
import json
from random import randint

from django_celery_beat.models import CrontabSchedule, PeriodicTask, PeriodicTasks
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# mongod db init and config
client = pymongo.MongoClient('localhost', 27017)
db = client.jobpost_data
db.posts.create_index('url', unique=True)
db.posts.create_index([('query_loc', 1), ('title', pymongo.TEXT),
                       ('skills', pymongo.TEXT), ('desc', pymongo.TEXT)])
# db.posts.index_information()
# db.posts.drop_index('query_loc_1_title_text_skills_text')
# import pdb; pdb.set_trace()  #### DEBUG


def all_tasks(request):
    auto_tasks = PeriodicTask.objects.all().select_related('crontab')
    i = app.control.inspect()
    running_tasks = list(i.active().values())[0]
    running_tasks.extend(list(i.reserved().values())[0])
    context = {'auto_tasks': auto_tasks, 'running_tasks': running_tasks}
    return render(request, 'home/all_tasks.html', context)


def manual_tasks(request):
    form = ScraperForm()
    scraper_list = []  # create scraper list with scraper info for display
    scraper_params = ScraperParams.objects.all().order_by('id')
    for scraper in scraper_params:
        status = {'status': 'NOT QUERIED YET',
                  'display_result': '',
                  'progress': 0}
        if scraper.task_id:
            task = TaskResult.objects.filter(task_id=scraper.task_id)
            if task.count() > 0:
                status['display_result'] = get_display_results(scraper.task_id)
                status['status'] = task[0].status
        scraper_list.append((scraper, status))

    context = {
        'form': form,
        'scraper_list': scraper_list,
    }
    return render(request, 'home/manual_tasks.html', context)


def reset_scraper_schedule(request):
    ## add city locations from file
    #with open(os.path.join(BASE_DIR, 'locs.json')) as f:
    #    locs = json.loads(f.read())
    #for loc in locs:
    #    QueryLoc.objects.create(
    #        name=loc,
    #        query=loc
    #    )

    # replace Crontab with many entries
    CrontabSchedule.objects.all().delete()
    days_to_scrape = [2, 5]
    for day in days_to_scrape:
        for hour in range(1,24):
            CrontabSchedule.objects.create(
                minute=randint(1,59),
                hour=hour,
                day_of_week=day
            )

    # replace PeriodicTask with one for each loc in db w/ random crontab
    PeriodicTask.objects.all().delete()
    for loc in QueryLoc.objects.all():
        for day in days_to_scrape:
            PeriodicTask.objects.create(
                crontab=CrontabSchedule.objects.filter(day_of_week=day).order_by('?').first(),
                name='scrape dice day#{} for: {}'.format(day, loc.query),
                task='djangosite.home.tasks.scrape_dice',
                kwargs=json.dumps({'query': '', 'query_loc': loc.query})
            )

    context = {'title': 'scraper was reset'}
    return render(request, 'home/index.html', context)


def index(request):
    ''' GET FORM DATA '''
    if request.method != 'GET':
        form = UserQueryForm()  # create new form
        context = {'title': 'enter query', 'results': [], 'form': form}
        return render(request, 'home/index.html', context)
    form = UserQueryForm(request.GET)  # populate form with data from request
    if not form.is_valid():
        form = UserQueryForm()
        context = {'title': 'enter query', 'results': [], 'form': form}
        return render(request, 'home/index.html', context)

    ''' QUERY DATABASE VIA USER KEYWORDS '''
    query = form.cleaned_data['query']
    query_loc = form.cleaned_data['location'].query.lower()
    dataset = db_text_search(query, query_loc)
    post_count = db.posts.find({'query_loc': query_loc}).count()

    ''' TEXT PROCESSING '''
    data = get_word_count(dataset)
    data = [tup for tup in data if tup[0] != query.lower()]
    words = [{'text': tup[0], 'size': tup[1]} for tup in data]

    ''' PREPARE DATA FOR TEMPLATE '''
    intro = '"{}" matches {}/{} job posts in last month [when not loc]. Highest occurring skills:'.format(
            query, len(dataset), post_count)
    context = {'intro': intro, 'data': data, 'form': form, 'words': words}
    return render(request, 'home/index.html', context)
