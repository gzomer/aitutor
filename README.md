# AI Tutor

## Inspiration

Covid-19 has severely disrupted education. Students need to learn more independently, and the lack of practising materials available may hinder their learning. Therefore, there is a need for solutions that help students to learn on their own.

## What it does

AI Tutor is a platform for students to learn on their own. It automatically generates questions for any content you like. Students can explore related content and add them to their materials list. Finally, students can view a dashboard with their grades and explore areas and subjects they need to improve on.

## How we built it

AI Tutor was built using Expert.AI api, Python, NLTK, WordNet, and MongoDB.

The main flow for generating questions given a content is:

- Student paste a content link
- The HTML content is downloaded
- A readability parser is used to extract the plain text from the document
- The main content is then sent to Expert.AI to extract:
	- Main sentences
	- Entities
	- Main lemmas
	- Main phrases
- The questions are generated based on the main sentences
- The answers are chosen from the main entitites/lemmas/phrases that have been ranked by score
- The distractors (wrong answers) are chosen by finding using Wordnet to find related words with different meaning

## Challenges we ran into

One of the biggest challenges was to find good distractors for the correct answer.

## Accomplishments that we're proud of

I'm proud of building a useful application that empower students to learn on their own.

## What's next for AI Tutor - Learn on your own

- Improve questions types
- Improve choice of distractors
- Improve related suggestions
- Create hierarchy of contents and subjects
