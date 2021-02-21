import os
import time
import json
import requests
import re

from time import sleep
from random import shuffle
from collections import defaultdict
from functools import partial
from urllib.parse import urljoin, urlparse

from pyquery import PyQuery as pq
from bson.objectid import ObjectId
from flask import Flask, render_template, request as req, redirect, session
from flask_pymongo import PyMongo
from flask_cors import CORS
from flask import jsonify
from expertai.nlapi.cloud.client import ExpertAiClient
from slugify import slugify

import html2text
from nltk import sent_tokenize
from readability import Document
# from transformers import pipeline

os.environ["EAI_USERNAME"] = 'gustavozomer@gmail.com'
os.environ["EAI_PASSWORD"] = 'Expertai$123'
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
app.config["MONGO_URI"] = "mongodb://localhost:27019/aitutor"
app.config['SECRET_KEY'] = 'AITUTOR_SECRETKEY'
mongo = PyMongo(app)
CORS(app)

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
    url = req.args.get('url')
    if not url:
        return redirect('/')

    content = mongo.db.Content.find_one({'url': url})

    if content:
        return redirect(f'/learn/{content["slug"]}/{content["_id"]}')
    else:
        html, text, title = get_content_from_url(url)
        relevant_terms = get_relevant_terms(text)
        questions = get_sentences(text, relevant_terms)

        for index, question in enumerate(questions, start=1):
            question['question'] = get_question(question['sentence'])
            question['id'] = index

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
            }
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
    tags = content['tags']['phrases'][:30]

    if req.method == 'GET':
        return render_template('learn.html', content=content, tags=tags)
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

        return render_template('learn.html', content=content, tags=tags)

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
    if session.get('user_id'):
        user = mongo.db.User.find_one({'_id':ObjectId(session.get('user_id'))})
        if user:
            ids = user['contents']

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
                print (answer)
                content = contents_map[answer['contentId']]
                relevant_tag = content['tags']['all'][0]
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

            scores['best'] = sorted(scores['all'], key=lambda x:x['score'], reverse=True)
            scores['worst'] = sorted(scores['all'], key=lambda x:x['score'])
            scores['total'] = [{'tag': key, 'score': value} for key, value in maps_total.items()]

    return render_template('dashboard.html', scores=scores)

def get_contents(search=None, ids=None):
    query = {}
    if ids:
        query['_id'] = {'$in': ids}

    if search:
        query['tags.all'] = search

    contents = mongo.db.Content.find(query)
    contents = list(contents)
    tags = list(set([tag for content in contents for tag in content['tags']['phrases']]))

    return contents, tags[:30]

def get_content_from_url(url):
    def srcrepl(base_url, match):
        absolute_link = urljoin(base_url, match.group(3))
        absolute_link = '/add?url=' + absolute_link
        return "<" + match.group(1) + match.group(2) + "=" + "\"" + absolute_link + "\"" + match.group(4) + ">"

    def relative_to_absolute_urls(fragment, base_url):
        p = re.compile(r"<(.*?)(src|href)=\"(?!http)(.*?)\"(.*?)>")
        absolute_fragment = p.sub(partial(srcrepl, base_url), fragment)
        return absolute_fragment

    response = requests.get(url)
    doc = Document(response.text)
    summary = doc.summary(html_partial=True)

    if 'wikipedia.org' in url:
        d = pq(summary)
        to_remove = ['.infobox', '.reflist','#References','#Further_reading','#See_also','.mw-editsection','.tright']

        for selector in to_remove:
            d(selector).remove()

        summary = d.html()

    try:
        parsed_url = urlparse(url)
        base_url = parsed_url.scheme + '://' + parsed_url.netloc
        summary = relative_to_absolute_urls(summary, base_url)
    except:
        pass

    content = html2text.html2text(summary)

    return summary, content, doc.title()

def get_categories(text):
    text = get_short_text(text)
    relevant_terms = []
    doc_taxonomies = client.classification(body={"document": {"text": text}}, params={'taxonomy': 'iptc','language': 'en'})#for item in doc_taxonomies.categories:
    relevant_terms.append({'label':item.label, 'score':item.frequency})
    return relevant_terms

def get_short_text(text):
    return text[:1000]

def get_relevant_terms(text):
    text = get_short_text(text)

    doc_entities = client.specific_resource_analysis(
        body={"document": {"text": text}},
        params={'language': 'en', 'resource': 'entities'
    })

    doc_relevants = client.specific_resource_analysis(
        body={"document": {"text": text}},
        params={'language': 'en', 'resource': 'relevants'
    })

    relevant_terms = []
    for item in doc_entities.entities:
        relevant_terms.append({'source': 'entity', 'label':item.lemma, 'type':item.type_})

    for item in doc_relevants.main_lemmas:
        relevant_terms.append({'source': 'lemma', 'label':item.value, 'score':item.score})

    for item in doc_relevants.main_phrases:
        relevant_terms.append({'source': 'phrases', 'label':item.value, 'score':item.score})

        for item in doc_relevants.main_sentences:
            relevant_terms.append({'source': 'sentences', 'label':item.value, 'score':item.score})

    for item in doc_relevants.main_syncons:
        relevant_terms.append({'source': 'syncons', 'label':item.lemma, 'score':item.score})

    return relevant_terms

def get_question(sentence):
    # nlp = pipeline("text2text-generation", model="valhalla/t5-small-qa-qg-hl")
    while (True):
        response = requests.post('https://api-inference.huggingface.co/models/valhalla/t5-base-qg-hl',json={
            "inputs": sentence
        })
        data = json.loads(response.text)
        if 'error' not in data:
            break
        else:
            sleep(2)

    return json.loads(response.text)[0]['generated_text']

def get_choices(term, terms):
    choices = [item
                for item in terms
                if item != term
                    and term not in item
                    and item not in term].copy()
    shuffle(choices)
    # TODO - Check type (%, number)
    return choices

def get_sentences(text, terms):
    sentences = sent_tokenize(text)

    shuffle(terms)

    selected_terms = [term['label'] for term in terms[:5]]

    candidate_questions = []

    used_terms = {}
    for sent in sentences:
        for term in selected_terms:
            if term in sent and not term in used_terms:
                choices = get_choices(term, selected_terms)
                full_choices = choices[:3] + [term]
                shuffle(full_choices)

                candidate_questions.append({
                    'sentence':sent.replace(term, f'<hl>{term}</hl>', 1),
                    'answer': term,
                    'choices': full_choices
                })
                used_terms[term] = True

    return candidate_questions

@app.route('/app')
def app_home():
    return app.send_static_file('app.html')

if __name__ == "__main__":
    app.run(host='0.0.0.0',port=5051, use_reloader=True, debug=True)