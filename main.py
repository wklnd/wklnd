import datetime
import hashlib
import os
import time

import requests
from dateutil import relativedelta
from lxml import etree

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
HEADERS = {'authorization': 'token ' + os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ.get('USER_NAME', 'wklnd')

BIRTHDAY = datetime.datetime(2000, 6, 7)
script_dir = os.path.dirname(os.path.abspath(__file__))

EXCLUDED_REPOS = {'wklnd/news-archive'}

QUERY_COUNT = {
    'user_getter': 0,
    'follower_getter': 0,
    'graph_repos_stars': 0,
    'recursive_loc': 0,
    'graph_commits': 0,
    'loc_query': 0,
}


# ---------------------------------------------------------------------------
# Time / formatting helpers
# ---------------------------------------------------------------------------

def daily_readme(birthday):
    """
    Returns the length of time since I was born.
    e.g. 'XX years, XX months, XX days'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    cake = ' 🎂' if (diff.months == 0 and diff.days == 0) else ''
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years,  'year'  + plural(diff.years),
        diff.months, 'month' + plural(diff.months),
        diff.days,   'day'   + plural(diff.days),
        cake,
    )


def plural(unit):
    return 's' if unit != 1 else ''


def perf_counter(funct, *args):
    """Runs a function and returns (result, elapsed_seconds)."""
    start = time.perf_counter()
    result = funct(*args)
    return result, time.perf_counter() - start


def formatter(label, elapsed, funct_return=False, whitespace=0):
    """Prints a formatted timing line."""
    print('{:<23}'.format('   ' + label + ':'), sep='', end='')
    if elapsed > 1:
        print('{:>12}'.format('%.4f' % elapsed + ' s '))
    else:
        print('{:>12}'.format('%.4f' % (elapsed * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def simple_request(func_name, query, variables):
    """Makes a GraphQL request and raises on failure."""
    request = requests.post(
        'https://api.github.com/graphql',
        json={'query': query, 'variables': variables},
        headers=HEADERS,
    )
    if request.status_code == 200:
        return request
    raise Exception(func_name, 'has failed with a', request.status_code, request.text, QUERY_COUNT)


def user_getter(username):
    """Returns the account ID and creation timestamp of a user."""
    query_count('user_getter')
    query = '''
    query($login: String!) {
        user(login: $login) {
            id
            createdAt
        }
    }'''
    request = simple_request(user_getter.__name__, query, {'login': username})
    data = request.json()['data']['user']
    return {'id': data['id']}, data['createdAt']


def follower_getter(username):
    """Returns the follower count of a user."""
    query_count('follower_getter')
    query = '''
    query($login: String!) {
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    return int(request.json()['data']['user']['followers']['totalCount'])


def graph_commits(start_date, end_date):
    """Returns the total contribution count between two dates."""
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date, 'end_date': end_date, 'login': USER_NAME}
    request = simple_request(graph_commits.__name__, query, variables)
    return int(
        request.json()['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions']
    )


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """Returns the total repository or star count."""
    query_count('graph_repos_stars')
    query = '''
    query($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(graph_repos_stars.__name__, query, variables)
    repos = request.json()['data']['user']['repositories']
    if count_type == 'repos':
        return repos['totalCount']
    elif count_type == 'stars':
        return stars_counter(repos['edges'])


def stars_counter(data):
    """Counts total stars across a list of repository edges."""
    return sum(node['node']['stargazers']['totalCount'] for node in data)


# ---------------------------------------------------------------------------
# Lines of code (LOC) counting
# ---------------------------------------------------------------------------

def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    """
    Queries all accessible repositories and returns the total lines of code.
    Fetches 60 repos at a time to avoid 502 timeouts.
    """
    if edges is None:
        edges = []
    query_count('loc_query')
    query = '''
    query($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    page = request.json()['data']['user']['repositories']
    edges += page['edges']
    if page['pageInfo']['hasNextPage']:
        return loc_query(owner_affiliation, comment_size, force_cache, page['pageInfo']['endCursor'], edges)
    return cache_builder(edges, comment_size, force_cache)


def recursive_loc(owner, repo_name, data, cache_comment):
    """
    Iteratively fetches all commits for a repo (100 at a time) and accumulates
    additions, deletions, and commit count for commits authored by me.
    """
    query = '''
    query($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    addition_total, deletion_total, my_commits = 0, 0, 0
    cursor = None

    while True:
        query_count('recursive_loc')
        variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
        request = requests.post(
            'https://api.github.com/graphql',
            json={'query': query, 'variables': variables},
            headers=HEADERS,
        )
        if request.status_code != 200:
            force_close_file(data, cache_comment)
            if request.status_code == 403:
                raise Exception('Too many requests in a short amount of time! You\'ve hit the anti-abuse limit.')
            raise Exception('recursive_loc() has failed with a', request.status_code, request.text, QUERY_COUNT)

        default_branch = request.json()['data']['repository']['defaultBranchRef']
        if default_branch is None:
            return 0

        history = default_branch['target']['history']
        for node in history['edges']:
            if node['node']['author']['user'] == OWNER_ID:
                my_commits += 1
                addition_total += node['node']['additions']
                deletion_total += node['node']['deletions']

        if not history['edges'] or not history['pageInfo']['hasNextPage']:
            return addition_total, deletion_total, my_commits

        cursor = history['pageInfo']['endCursor']


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def cache_path():
    return 'cache/' + hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest() + '.txt'


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Checks each repository against the cache and updates LOC if commits have changed.
    """
    cached = True
    filename = cache_path()
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError:
        data = []
        if comment_size > 0:
            data = ['This line is a comment block. Write whatever you want here.\n'] * comment_size
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data) - comment_size != len(edges) or force_cache:
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size]
    data = data[comment_size:]

    for index in range(len(edges)):
        repo_name_with_owner = edges[index]['node']['nameWithOwner']
        if repo_name_with_owner in EXCLUDED_REPOS:
            data[index] = hashlib.sha256(repo_name_with_owner.encode('utf-8')).hexdigest() + ' 0 0 0 0\n'
            continue
        repo_hash, commit_count, *__ = data[index].split()
        expected_hash = hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest()
        if repo_hash == expected_hash:
            try:
                actual_count = edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']
                if int(commit_count) != actual_count:
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')
                    loc = recursive_loc(owner, repo_name, data, cache_comment)
                    data[index] = '{} {} {} {} {}\n'.format(repo_hash, actual_count, loc[2], loc[0], loc[1])
            except TypeError:
                data[index] = repo_hash + ' 0 0 0 0\n'

    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)

    for line in data:
        parts = line.split()
        loc_add += int(parts[3])
        loc_del += int(parts[4])

    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    """Wipes the cache (called when repo count changes or on first run)."""
    with open(filename, 'r') as f:
        data = f.readlines()[:comment_size] if comment_size > 0 else []
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    """Saves partial cache data before a crash."""
    filename = cache_path()
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('Error during cache write. Partial data saved to', filename)


def commit_counter(comment_size):
    """Counts total commits from the cache file."""
    filename = cache_path()
    with open(filename, 'r') as f:
        data = f.readlines()
    data = data[comment_size:]
    return sum(int(line.split()[2]) for line in data)


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    """Updates the SVG file with fresh stats."""
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'commit_data',   commit_data,      22)
    justify_format(root, 'star_data',     star_data,        14)
    justify_format(root, 'repo_data',     repo_data,         6)
    justify_format(root, 'contrib_data',  contrib_data)
    justify_format(root, 'follower_data', follower_data,    10)
    justify_format(root, 'loc_data',      loc_data[2],       9)
    justify_format(root, 'loc_add',       loc_data[0])
    justify_format(root, 'loc_del',       loc_data[1],       7)
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    """Updates an SVG element's text and adjusts the preceding dot padding."""
    if isinstance(new_text, int):
        new_text = '{:,}'.format(new_text)
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    dot_map = {0: '', 1: ' ', 2: '. '}
    dot_string = dot_map[just_len] if just_len <= 2 else ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    """Finds an SVG element by ID and updates its text."""
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print('Calculation times:')

    user_data, user_time = perf_counter(user_getter, USER_NAME)
    OWNER_ID, acc_date = user_data
    formatter('account data', user_time)

    age_data, age_time = perf_counter(daily_readme, BIRTHDAY)
    formatter('age calculation', age_time)

    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    formatter('LOC (cached)' if total_loc[-1] else 'LOC (no cache)', loc_time)

    commit_data,   commit_time   = perf_counter(commit_counter, 7)
    star_data,     star_time     = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data,     repo_time     = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data,  contrib_time  = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, USER_NAME)

    for index in range(len(total_loc) - 1):
        total_loc[index] = '{:,}'.format(total_loc[index])

    print("Writing to SVG...")
    svg_overwrite(os.path.join(script_dir, 'dark_mode.svg'),  age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite(os.path.join(script_dir, 'light_mode.svg'), age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    print("Done!")

    total_time = user_time + age_time + loc_time + commit_time + star_time + repo_time + contrib_time + follower_time
    print('\033[F\033[F\033[F\033[F\033[F\033[F\033[F\033[F',
          '{:<21}'.format('Total function time:'), '{:>11}'.format('%.4f' % total_time),
          ' s \033[E\033[E\033[E\033[E\033[E\033[E\033[E\033[E', sep='')

    print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
    for funct_name, count in QUERY_COUNT.items():
        print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))