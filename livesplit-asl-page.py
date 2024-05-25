#!/usr/bin/env python3

# as of 2024 this code was written almost a decade ago so yeah...

import json
import os
import re
import sys
from enum import Enum
from xml.etree import ElementTree

import requests
# disabled for github ci
#from cachecontrol import CacheControl
#from cachecontrol.caches import FileCache
from jinja2 import Template

DIR = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(DIR, 'docs', 'index.html')
XML_URL = 'https://raw.githubusercontent.com/LiveSplit/LiveSplit.AutoSplitters/master/LiveSplit.AutoSplitters.xml'

LOC_SMALL = 15
LOC_BIG = 20


def loc_filter(line):
    if len(line) == 0 or line.isspace():
        return False
    if line.lstrip().startswith("//"):
        return False
    return True

def extract_methods(src):
    # remove single line comments
    lines = []
    for line in src.splitlines():
        line = line.strip()
        comment_index = line.find("//")
        if comment_index != -1:
            line = line[:comment_index]
        lines.append(line)
    src = '\n'.join(lines)

    end = len(src)
    i = 0

    def block(start, stop):
        nonlocal i, end

        start_len = len(start)
        stop_len = len(stop)

        i += start_len
        stack = 1

        while i < end and stack > 0:
            if src[i:i+start_len] == start:
                stack += 1
                i += start_len
            elif src[i:i+stop_len] == stop:
                stack -= 1
                i += stop_len
            else:
                i += 1

    ret = {}
    method_name = ''
    while i < end:
        if src[i] == '{':
            name = method_name
            method_name = ''

            start = i+1
            block('{', '}')
            body = src[start:i-1].strip()

            if name != 'state':
                ret[name] = body

        elif src[i] == '(':
            block('(', ')')
        elif src[i:i+1] == '/*':
            block('/*', '*/')
        elif src[i].isspace():
            i += 1
        else:
            method_name += src[i]
            i += 1
    return ret

def download_asls():
    #sess = CacheControl(requests.session(), cache=FileCache(os.path.join(DIR, 'cache')))
    sess = requests.session()

    print('get xml')
    r = sess.get(XML_URL, timeout=30)
    xml = ElementTree.fromstring(r.content)
   
    asls = []

    for component in xml.findall('AutoSplitter'):
        try:
            game =  component.find('Games/Game').text
            if component.find('Type').text != 'Script':
                continue
            game =  component.find('Games/Game').text
            url = component.find('URLs/URL').text
            
            if url.endswith('.wasm'):
                continue

            try:
                print('get ' + url)
                r2 = sess.get(url, timeout=30)
                r2.raise_for_status()
            except Exception as e:
                print(e, file=sys.stderr)
                continue
            source = r2.content.decode('utf-8', 'ignore')
            loc = len(list(filter(loc_filter, source.splitlines())))

            description = component.find('Description').text

            author = re.search(r'\(By ([^)]+)', description, re.I)
            author = author.group(1) if author else '?'
            if author == '?':
                m = re.search(r'githubusercontent.com/([^/]+)', url)
                if m:
                    author = m.group(1)
            
            website = component.find('Website')
            website = None if website is None else website.text

            asls.append({
                'game': game,
                'url': url,
                'description': description,
                'author': author,
                'website': website,
                'source': source,
                'loc': loc,
            })
        except Exception as ex:
            print('a component failed : ' + str(ex), file=sys.stderr)

    return asls 

class Feature(Enum):
    start = 'Start'
    onstart = 'On Start'
    reset = 'Reset'
    onreset = 'On Reset'
    split = 'Split'
    loads = 'Load Removal'
    startup = 'Startup'
    update = 'Update'
    init = 'Init'
    igt = 'Game Time'
    
class Behaviour(Enum):
    scanner = 'SignatureScanner'
    versions = 'Detects Versions'
    memwrite = 'Writes Memory'
    memwatcher = 'Memory Watcher'
    output = 'Debug Output'
    comments = 'Comments'
    settings = 'Settings'
    functions = 'Functions'
    mempages = 'Memory Pages'
    
def tag_asls(asls):
    behaviour_searches = {
        'SignatureScanner': Behaviour.scanner,
        'version =': Behaviour.versions,
        '.WriteBytes': Behaviour.memwrite,
        'MemoryWatcher': Behaviour.memwatcher,
        #'print(': Behaviour.output,
        #'//': Behaviour.comments,
        #'/*': Behaviour.comments,
        'settings.Add': Behaviour.settings,
        'Func<': Behaviour.functions,
        'Action<': Behaviour.functions,
        '.MemoryPages': Behaviour.mempages,
    }

    for asl in asls:
        features  = []
        behaviours = []

        src = asl['source']
        loc = asl['loc']

        # TODO: remove all comments before doing anything at all
        # TODO: remove multi line comments from loc

#        if loc < LOC_SMALL:
#            tags.append('small')
#        elif loc > LOC_BIG:
#            tags.append('big')

        for search, behaviour in behaviour_searches.items():
            if search in src:
                behaviours.append(behaviour)

        methods = extract_methods(src)
        for name, body in methods.items():
            if len(body) == 0:
                continue

            def returns():
                nonlocal body
                return not ('return false' in body and body.count('return') == 1)

            if name == 'isLoading' and returns():
                features.append(Feature.loads)
            elif name == 'start' and returns():
                features.append(Feature.start)
            elif name == 'onStart' and returns():
                features.append(Feature.onstart)
            elif name == 'reset' and returns():
                features.append(Feature.reset)
            elif name == 'onReset' and returns():
                features.append(Feature.onreset)
            elif name == 'split' and returns():
                features.append(Feature.split)
            elif name == 'startup' and returns():
                features.append(Feature.startup)
            elif name == 'update' and returns():
                features.append(Feature.update)
            elif name == 'init' and returns():
                features.append(Feature.init)
            elif name == 'gameTime':
                features.append(Feature.igt)

        if Feature.igt in features and Feature.loads in features:
            features.remove(Feature.loads)
        asl['features'] = features
        asl['behaviours'] = behaviours

def detect_complexity(asls):
    behaviour_points = {
        Behaviour.scanner: 4, # sigscan
        #Behaviour.mempages: 2,
        Behaviour.memwrite: 5, # code payloads/hooks
        Behaviour.memwatcher: 2,
        Behaviour.settings: 1,
        Behaviour.functions: 1,
        Behaviour.versions: 2,
    }
    points_per_loc = (1/75.0)

    for asl in asls:
        # 1 point per method implemented
        points = len(asl['features'])

        for behaviour in asl['behaviours']:
            points += behaviour_points.get(behaviour, 0)

        #points += int(asl['loc'] * points_per_loc)

        asl['complexity'] = points

def render(asls):
    asls.sort(key=lambda x: x['game'].lower())

    with open(os.path.join(DIR, 'template.html')) as f:
        template = Template(f.read())
        rendered = template.render(asls=asls)
    with open(FILE, 'w') as f:
        f.write(rendered)

if __name__ == '__main__':
    asls = download_asls()
    tag_asls(asls)
    detect_complexity(asls)

    for asl in asls:
        asl['features'] = [f.value for f in asl['features']]
        asl['behaviours'] = [b.value for b in asl['behaviours']]
    render(asls)
