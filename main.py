import sys
import click
import requests
import json
import datetime
#import concurrent.futures
from yaspin import yaspin
from yaspin.spinners import Spinners
from prettytable import PrettyTable
from getpass import getpass

class Config(object):
    def __init__(self):
        self.auth_header = {}
        self.gitlab = None
        self.project_id = '4750040'
        self.issue_filter_issue_id = None
        self.verbose = False
        self.issue_filter_time = None
        self.output_file = None
        self.lane_labels = ['workflow::selected', 'workflow::todo', 'workflow::wip', 'workflow::review', 'workflow::testing']


pass_config = click.make_pass_decorator(Config, ensure=True)

@click.command()
@click.option('-V', '--verbose', is_flag=True, help='Verbose output', required=False)
@click.option('-d', '--days', default=14, type=int, help='Set time range for issues', required=False)
@click.option('--file', type=str, help='File for output', required=False)
@click.argument('issue_id', type=str, required=False)
@pass_config
def cli(config, verbose, days, file, issue_id):
    config.verbose = verbose
    config.output_file = file
    config.issue_filter_issue_id = issue_id
    config.issue_filter_time = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    # click.echo('Analyse %s issues.' % config.issues_state)
    click.echo('Analyse issues back to %s' % config.issue_filter_time)

    config.gitlab = GitlabAPI(config.project_id)
    config.gitlab.login()
    issues = config.gitlab.getIssues(iid=config.issue_filter_issue_id, updated_after=config.issue_filter_time)
    click.echo('Fetched %s issues.' % len(issues))
    labels_by_id_by_issue = getLabelsWithDuration(config, issues) # issue_id -> Label
    average_labels_by_id = analyseLabels(config, labels_by_id_by_issue)
    table = printAverageLabels(average_labels_by_id)

    if config.output_file is not None:
        f = open(config.output_file, "w")
        f.write(table.get_string())
        f.close()

def analyseLabels(config, labels_by_id_by_issue):
    average_labels_by_id = {}
    label: Label
    for issue_id in labels_by_id_by_issue.keys():
        for label_id in labels_by_id_by_issue[issue_id].keys():
            label = labels_by_id_by_issue[issue_id][label_id]
            if label_id in average_labels_by_id.keys():
                average_labels_by_id[label_id].durations.append(label.duration)
            else:
                average_labels_by_id[label_id] = LabelAverage(label_id, label.label_name, label.duration)
    return average_labels_by_id

# TODO: threading
def getLabelsWithDuration(config, issues):
    with yaspin(Spinners.earth, text="Fetching label events") as sp:
        labels_by_id_by_issue = {}
        count = 1
        for issue in issues:
            sp.text = 'Fetching label events %s/%s' % (count, len(issues))
            issue_id = issue['iid']
            label_events = config.gitlab.getIssueLabelEvents(issue_id)
            labels_by_id = parseLabelEvents(config, issue, label_events)
            if config.verbose:
                printIssueLabelAnalysis(issue_id, labels_by_id)

            labels_by_id_by_issue[issue_id] = labels_by_id
            count += 1
        sp.ok('✔')
        return labels_by_id_by_issue

def printIssueLabelAnalysis(issue_id, labels_by_id):
    print('\n>> Label Analysis for Issue %s <<' % issue_id)
    table = PrettyTable(['Name', 'Time'])
    table.align['Name'] = 'l'
    table.align['Time'] = 'r'
    label: Label
    for id in labels_by_id.keys():
        label = labels_by_id[id]
        table.add_row([label.label_name, label.duration])
    table.sortby = 'Name'
    table.reversesort = True
    print(table)
    return table

def printAverageLabels(labels):
    print('\n>> Analysis <<')
    table = PrettyTable(['Name', 'Average Time'])
    table.align['Name'] = 'l'
    table.align['Average Time'] = 'r'
    for label_id in labels.keys():
        average = calcAverage(labels[label_id].durations)
        table.add_row([labels[label_id].label_name, average])
    table.sortby = 'Name'
    table.reversesort = True
    print(table)
    return table

def calcAverage(durations):
    return sum(durations, datetime.timedelta()) / len(durations)

def parseLabelEvents(config, issue, label_events):
    label_events.sort(key=lambda x: x['created_at'], reverse=False)
    labels_by_id = {}
    for raw_event in label_events:
        if  'id' in raw_event and \
            'action' in raw_event and \
            'created_at' in raw_event and \
            'label' in raw_event:
            event_label = raw_event['label']
            if  event_label is not None and \
                'id' in event_label and \
                'name' in event_label:
                    action = raw_event['action']
                    created_at = raw_event['created_at']
                    label_name =event_label['name']
                    label_id = event_label['id']
            else:
                if config.verbose:
                    print('Error in label event:', raw_event)
                continue
        else:
            if config.verbose:
                print('Error in label event:', raw_event)
            continue

        if config.verbose:
            print('%s label %s (%s) at %s' %(action, label_name, label_id, created_at))

        if label_id in labels_by_id.keys():
            label = labels_by_id[label_id]
            if action == 'add' and label.completed == True:
                label.added_at = created_at
                label.completed = False
            elif action == 'remove' and label.completed == False:
                label.duration += calcDuration(label.added_at, created_at)
                label.completed = True
        elif action == 'add':
            labels_by_id[label_id] = Label(label_id, label_name, created_at)
        elif action == 'remove':
            labels_by_id[label_id] = Label(label_id, label_name, created_at)
            labels_by_id[label_id].duration += calcDuration(issue['created_at'], created_at)
            labels_by_id[label_id].completed = True

    for x in labels_by_id.keys():
        label: Label = labels_by_id[x]
        if label.completed == False and issue['state'] == 'closed':
            label.duration = calcDuration(label.added_at, issue['closed_at'])
            label.completed = True
            labels_by_id[label.label_id] = label
        elif label.completed == False and issue['state'] == 'opened':
            label.duration = calcDuration(label.added_at, datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ'))
            label.completed = True
            labels_by_id[label.label_id] = label
    return labels_by_id


def calcDuration(start, end):
    return datetime.datetime.strptime(end, '%Y-%m-%dT%H:%M:%S.%fZ') - datetime.datetime.strptime(start, '%Y-%m-%dT%H:%M:%S.%fZ')


class Label:
    duration = datetime.timedelta(0)
    completed = False
    def __init__(self, label_id, label_name, added_at):
        self.label_id = label_id
        self.label_name = label_name
        self.added_at = added_at

class LabelAverage():
    average = None
    def __init__(self, label_id, label_name, duration):
        self.label_id = label_id
        self.label_name = label_name
        self.durations = [duration]

    
class GitlabAPI:

    auth_header = None
    base_url = 'https://gitlab.com/'
    api_url = base_url + 'api/v4/'
    auth_url = base_url + 'oauth/token'

    def __init__(self, project_id):
        self.project_id = project_id

    def project_url(self):
        return self.api_url + 'projects/%s/' % self.project_id

    def issues_url(self):
        return self.project_url() + 'issues'

    def issue_label_url(self, issue_id):
        return '%s/%s/%s' % (self.issues_url(), issue_id, 'resource_label_events')

    def login(self):
        username = input('Gitlab username: ')
        password = getpass()
        auth = json.dumps({
            "grant_type"    : "password",
            "username"      : username,
            "password"      : password
        })
        r = requests.post(self.auth_url, json=json.loads(auth))
        auth_res = json.loads(r.text)
        if 'error' in auth_res:
            print(auth_res)
            sys.exit(1)
        self.auth_header = { "Authorization": auth_res["token_type"] + " " + auth_res["access_token"] }

    def getIssueLabelEvents(self, issue_id):
        url = self.issue_label_url(issue_id)
        issue_labels = []
        try:
            response = requests.get(url, headers=self.auth_header)
            response.raise_for_status()
            issue_labels = json.loads(response.text)
        except requests.HTTPError as exception:
            print(exception)
            sys.exit(1)
        return issue_labels

    def getIssues(self, iid=None, state=None, labels=None, updated_after=None):
        with yaspin(Spinners.earth, text="Fetching issues") as sp:
            url = self.issues_url() + '?per_page=100&'

            if iid is not None:
                url += 'iids[]=%s&' % iid
            if state is not None:
                url += 'state=%s&' % state
            if labels is not None:
                url += 'labels=%s&' % labels
            if updated_after is not None:
                url += 'updated_after=%s&' % updated_after

            # TODO: Pagination with threading (after first request page_count is known).
            all_issues = []
            pagination_page = '1'
            last_page = ''
            while(pagination_page != ''):
                try:
                    if pagination_page != '1':
                        sp.text = 'Fetching issues %s/%s' % (pagination_page, last_page)
                    pageUrl = url + 'page=' + pagination_page
                    response = requests.get(pageUrl, headers=self.auth_header)
                    response.raise_for_status()
                    headers = response.headers
                    response_json = json.loads(response.text)
                    all_issues += response_json
                    pagination_page = headers['X-Next-Page']
                    last_page =  headers['X-Total-Pages']
                except requests.HTTPError as exception:
                    sp.fail('💥')
                    print(exception)
                    sys.exit(1)
            sp.ok("✔")
            return all_issues

        
        