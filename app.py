import os
import time
import json
import requests
import re
import hashlib
import time

from os import path
from time import sleep
from random import shuffle
from collections import defaultdict
from functools import partial
from urllib.parse import urljoin, urlparse

from pyquery import PyQuery as pq
from bs4 import BeautifulSoup
from bson.objectid import ObjectId
from flask import Flask, render_template, request as req, redirect, session
from flask_pymongo import PyMongo
from flask_cors import CORS
from flask import jsonify
from expertai.nlapi.cloud.client import ExpertAiClient
from slugify import slugify
from nltk.corpus import wordnet as wn

import html2text
from nltk import sent_tokenize
from readability import Document

client = ExpertAiClient()

class CustomFlask(Flask):
    jinja_options = Flask.jinja_options.copy()
    jinja_options.update(dict(
        block_start_string='<%',
        block_end_string='%>',
        variable_start_string='[[',
        variable_end_string=']]',
        comment_start_string='<#',
        comment_end_string='#>',
    ))

app = CustomFlask(__name__)
app.config["MONGO_URI"] = "mongodb://localhost:27017/aitutor"
app.config['SECRET_KEY'] = 'AITUTOR_SECRETKEY'
mongo = PyMongo(app)
CORS(app)
_SHOULD_ADD = True

@app.before_request
def handle_user_auth():
    should_create = True
    if session.get('user_id'):
        user = mongo.db.User.find_one({'_id':ObjectId(session.get('user_id'))})
        if user:
            should_create = False

    if should_create:
        user = mongo.db.User.insert_one({'contents':[]})
        session['user_id'] = str(user.inserted_id)

@app.route('/add', methods=["GET", "POST"])
def add_content():

    def is_url_valid(url):
        regex = re.compile(
        r'^(?:http|ftp)s?://' # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' #domain...
        r'localhost|' #localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
        r'(?::\d+)?' # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)

        return re.match(regex, url) is not None

    url = req.args.get('url')
    if not url or not is_url_valid(url):
        return redirect('/')

    content = mongo.db.Content.find_one({'url': url})

    if content:
        return redirect(f'/learn/{content["slug"]}/{content["_id"]}')
    else:
        html, text, title = get_content_from_url(url)

        relevant_terms = get_document_terms(text, url)
        questions = get_questions(text, relevant_terms)

        slug = slugify(title)

        sentences = [term['label'] for term in relevant_terms if term['source'] == 'sentences']

        data = {
            'title': title,
            'description': sentences[0] if sentences else ' '.join(text.split(' ')[:100]),
            'slug': slug,
            'html': html,
            'text': text,
            'url': url,
            'questions': questions,
            'terms': relevant_terms,
            'tags': {
                'all': [term['label'].lower() for term in relevant_terms if term['source'] in ('entity', 'syncons','lemma','phrases')],
                'entity': [term['label'].lower() for term in relevant_terms if term['source'] == 'entity'],
                'syncons': [term['label'].lower() for term in relevant_terms if term['source'] == 'syncons'],
                'lemma': [term['label'].lower() for term in relevant_terms if term['source'] == 'lemma'],
                'phrases': [term['label'].lower() for term in relevant_terms if term['source'] == 'phrases']
            },
            'created_at': round(time.time()),
        }

        content = mongo.db.Content.insert_one(data)

        if session.get('user_id'):
            mongo.db.User.update_one(
                {'_id': ObjectId(session.get('user_id'))},
                {'$addToSet': {
                    'contents': content.inserted_id
                }}
            )

        return redirect(f'/learn/{slug}/{content.inserted_id}')

@app.route('/learn/<title>/<id>', methods=["GET", "POST"])
def learn(title, id):
    content = mongo.db.Content.find_one({'_id': ObjectId(id)})
    tags = content['tags']['phrases'][:20]
    update_questions = False
    limit_questions = 10

    if update_questions:
        url = content['url']
        html, text, title = get_content_from_url(url)
        relevant_terms = get_document_terms(text, url)
        questions = get_questions(text, relevant_terms)

        content['questions'] = questions

    content['questions'] = content['questions'][:limit_questions]

    related_content, _ = get_contents(tags[:10], ids=None, limit=10)
    related_content = [item for item in related_content if item['_id'] != content['_id']]

    if req.method == 'GET':
        return render_template('learn.html', content=content, tags=tags, related_content=related_content)
    else:
        questions = content['questions']

        answers = {key.replace('question-',''):value for key, value in dict(req.form).items()}

        correct = 0
        wrong = 0

        for question in questions:
            if answers.get(str(question['id'])):
                question['selected_answer'] = answers.get(str(question['id']))

            if not question.get('selected_answer'):
                question['error'] = "You need to answer this question"
            elif question.get('selected_answer') != question['answer']:
                wrong += 1
                question['error'] = f"Wrong answer, the correct answer is: {question['answer']}."
            elif question.get('selected_answer') == question['answer']:
                correct += 1
                question['success'] = True

        content['questions'] = questions

        if session.get('user_id'):
            mongo.db.Answer.delete_one({'contentId': id, 'userId': session.get('user_id')})
            mongo.db.Answer.insert_one({
                'contentId': id,
                'userId': session.get('user_id'),
                'stats': {
                    'correct': correct,
                    'wrong': wrong
                },
                'answers': answers
            })

            if session.get('user_id'):
                mongo.db.User.update_one(
                    {'_id': ObjectId(session.get('user_id'))},
                    {'$addToSet': {
                        'contents': ObjectId(id)
                    }}
                )

        return render_template('learn.html', content=content, tags=tags, related_content=related_content)

@app.route('/link', methods=["GET", "POST"])
def link_content():
    url = req.args.get('url')
    content = mongo.db.Content.find_one({'url': url})

    if content:
        return redirect(f'/learn/{content["slug"]}/{content["_id"]}')
    elif _SHOULD_ADD:
        return redirect(f'/add?url={url}')
    else:
        return redirect(url)

@app.route('/')
def home():
    contents, tags = get_contents()
    return render_template('home.html', hide_search=True, contents=contents, tags=tags)

@app.route('/explore/')
@app.route('/explore/<search>')
def explore(search=None):
    contents, tags = get_contents(search)
    return render_template('contents.html', explore_url='explore', title='Explore', contents=contents, tags=tags)

@app.route('/contents/')
@app.route('/contents/<search>')
def contents(search=None):
    ids = []
    contents = []
    tags = []

    if session.get('user_id'):
        user = mongo.db.User.find_one({'_id':ObjectId(session.get('user_id'))})
        if user:
            ids = user['contents']

    if ids:
        contents, tags = get_contents(search, ids)

    return render_template('contents.html', explore_url='contents', title='My Materials', contents=contents, tags=tags)

@app.route('/dashboard')
def dashboard():
    scores = {
        'all': []
    }

    if session.get('user_id'):
        answers = mongo.db.Answer.find({'userId':session.get('user_id')})
        if answers:
            answers = list(answers)
            ids = [ObjectId(answer['contentId']) for answer in answers]
            contents = mongo.db.Content.find({'_id':{'$in': ids}}, {'tags':1})

            contents_map = {str(content['_id']):content for content in contents}

            # TODO - Improve
            maps_score_correct = defaultdict(int)
            maps_score_wrong = defaultdict(int)
            maps_score = defaultdict(int)
            maps_total = defaultdict(int)

            for answer in answers:
                content = contents_map[answer['contentId']]
                relevant_tag = content['tags']['phrases'][0]
                maps_score_correct[relevant_tag] += answer['stats']['correct']
                maps_score_wrong[relevant_tag] += answer['stats']['wrong']

            for key in list(set(list(maps_score_correct.keys()) + list(maps_score_wrong.keys()))):
                correct = maps_score_correct.get(key, 0)
                wrong = maps_score_wrong.get(key, 0)
                total = correct + wrong
                if total:
                    maps_score[key] = (correct/total)*100
                if total:
                    maps_total[key] = total

            scores['all'] = [{'tag': key, 'score': value} for key, value in maps_score.items()]

            scores['best'] = sorted([item for item in scores['all'] if item['score'] >= 70], key=lambda x:x['score'], reverse=True)
            scores['worst'] = sorted([item for item in scores['all'] if item['score'] < 70], key=lambda x:x['score'])
            scores['total'] = [{'tag': key, 'score': value} for key, value in maps_total.items()][:15]
            scores['less'] = sorted([{'tag': key, 'score': value} for key, value in maps_total.items()], key=lambda x:x['score'])[:15]

    return render_template('dashboard.html', scores=scores)

def get_contents(search=None, ids=None, limit=50):
    query = {}
    if ids:
        query['_id'] = {'$in': ids}

    if search:
        if isinstance(search, list):
            query['tags.all'] = {'$in': search}
        else:
            query['tags.all'] = search

    contents = mongo.db.Content.find(query, {'_id':1, 'title':1, 'slug':1, 'tags.phrases':1, 'description':1}).limit(limit)
    contents = list(contents)
    tags = list(set([tag for content in contents for tag in content['tags']['phrases']]))

    return contents, tags[:15]

def get_cache_key(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def get_content_from_url(url):
    def srcrepl(base_url, match):
        absolute_link = urljoin(base_url, match.group(3))
        absolute_link = '/link?url=' + absolute_link
        return "<" + match.group(1) + match.group(2) + "=" + "\"" + absolute_link + "\"" + match.group(4) + ">"

    def relative_to_absolute_urls(fragment, base_url):
        p = re.compile(r"<(.*?)(src|href)=\"(?!http)(.*?)\"(.*?)>")
        absolute_fragment = p.sub(partial(srcrepl, base_url), fragment)
        return absolute_fragment

    file_cache = f'./cache/sites/{get_cache_key(url)}.html'

    if not path.exists(file_cache):
        response = requests.get(url)
        text = response.text
        with open(file_cache, 'w') as f:
            f.write(text)
    else:
        with open(file_cache) as f:
            text = str(f.read())

    doc = Document(text)
    summary = doc.summary(html_partial=True)

    if 'wikipedia.org' in url:
        d = pq(summary)
        to_remove = ["#External_links","#General_information","#Experiments","#Online_lectures", '.spoken-wikipedia', '#Bibliography', '.book', '.refbegin', '.shortdescription', '.reference', '.infobox', '.reflist', '#References','#Further_reading','#See_also','.mw-editsection','.tright']

        def check_link(index, a):
            da = pq(a)

            if da.attr('href') and '#cite_' in da.attr('href'):
                da.remove()

        d('a').each(check_link)

        for selector in to_remove:
            d(selector).remove()

        summary = d.html()

    try:
        parsed_url = urlparse(url)
        base_url = parsed_url.scheme + '://' + parsed_url.netloc
        summary = relative_to_absolute_urls(summary, base_url)
    except:
        pass

    soup = BeautifulSoup(summary, features="lxml")
    content = soup.get_text().rstrip('\n')
    content = re.sub(r'\n+', '\n', content).strip()

    return summary, content, doc.title()

def get_relevant_terms(text):

    analysis = client.full_analysis(
        body={"document": {"text": text}},
        params={'language': 'en'}
    )

    relevant_terms = []
    for item in analysis.entities:
        relevant_terms.append({'source': 'entity', 'label':item.lemma, 'type':item.type_})

    for item in analysis.main_lemmas:
        relevant_terms.append({'source': 'lemma', 'label':item.value, 'score':item.score})

    for item in analysis.main_phrases:
        relevant_terms.append({'source': 'phrases', 'label':item.value, 'score':item.score})

        for item in analysis.main_sentences:
            relevant_terms.append({'source': 'sentences', 'label':item.value, 'score':item.score})

    for item in analysis.main_syncons:
        relevant_terms.append({'source': 'syncons', 'label':item.lemma, 'score':item.score})

    return relevant_terms

def get_document_terms(text, url):
    file_cache = f'./cache/terms/{get_cache_key(url)}.json'
    terms = []

    if path.exists(file_cache):
        with open(file_cache) as f:
            terms = json.loads(str(f.read()))
    else:
        paragraphs = text.split('\n')
        max_length = 20000
        limit_reached = False
        batch_size = 3000
        current_batch = ''
        total_length = 0

        for p in paragraphs:
            sents = [s for s in sent_tokenize(p) if len(s.split()) > 7]

            for s in sents:
                max_length += len(s)

                if total_length + len(s) > max_length:
                    limit_reached = True
                    break

                if len(current_batch) + len(s) > batch_size:
                    terms.extend(get_relevant_terms(current_batch))
                    current_batch = ''
                current_batch += f'{s} '

            if limit_reached:
                break

            if len(current_batch) + len(s) > batch_size:
                terms.extend(get_relevant_terms(current_batch))
                current_batch = ''

        terms = list({term['label']:term for term in terms}.values())
        with open(file_cache, 'w') as f:
            f.write(json.dumps(terms, indent=2))

    return terms


def get_questions(text, terms):
    sentences = [term['label'] for term in terms if term['source'] == 'sentences']
    shuffle(terms)

    selected_terms = [term['label'] for term in terms if len(term['label'].split()) == 1]

    candidate_questions = []

    used_terms = {}
    for sent in sentences:
        for term in selected_terms:
            if any([term == word for word in sent.split()]) and not term in used_terms:
                choices = get_choices(term)
                if not choices or len(choices) < 3:
                    continue

                full_choices = choices[:3] + [term]
                shuffle(full_choices)

                candidate_questions.append({
                    'sentence':sent.replace(term, f' <hl>{term}</hl>', 1),
                    'sentence_cloze': re.sub(rf'\b{term}\b', ' ________ ', sent),
                    'answer': term,
                    'choices': full_choices
                })
                used_terms[term] = True
                break

    for index, question in enumerate(candidate_questions, start=1):
        question['question'] = question['sentence_cloze']
        question['id'] = index

    return candidate_questions


def get_choices(word):
    _MAX_SIMILAR = 10
    choices = []
    synsets = wn.synsets(word, pos='n')

    if len(synsets) == 0:
        return []
    else:
        first_synset = synsets[0]

    hypernyms = first_synset.hypernyms()
    if len(hypernyms) <= 0:
        return []

    first_hypernym = hypernyms[0]

    for hyponym in first_hypernym.hyponyms():
        lemmas = hyponym.lemmas()
        first_lemma = lemmas[0].name()
        similar = first_lemma.replace('_', ' ')

        if similar != word:
            choices.append(similar)

    return [similar for similar in choices if similar not in word][:_MAX_SIMILAR]

@app.route('/app')
def app_home():
    return app.send_static_file('app.html')

if __name__ == "__main__":
    app.run(host='0.0.0.0',port=5051, use_reloader=True, debug=True)