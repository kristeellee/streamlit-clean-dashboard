# -*- coding: utf-8 -*-
"""Dashboard.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1qtKgYd0P0oefwdZbtfE2A-gdnN0nMOMP
"""

import streamlit as st
import pandas as pd
import numpy as np
import praw
import malaya
import re, emoji, contractions
import nltk
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords, wordnet
from nltk import pos_tag
from nltk.stem import WordNetLemmatizer
import malaya.text.function as malaya_text
from bertopic import BERTopic
from sklearn.feature_extraction.text import CountVectorizer
from gensim.corpora import Dictionary
from gensim.models import CoherenceModel
import pickle
import time
from wordcloud import WordCloud
import matplotlib.pyplot as plt

# --- NLTK resources and models loading (Suggested to be cached) ---
@st.cache_resource
def load_nlp_assets():
    nltk.download('punkt', quiet=True)
    nltk.download('stopwords', quiet=True)
    nltk.download('wordnet', quiet=True)
    nltk.download('averaged_perceptron_tagger_eng', quiet=True)

    lemmatizer = WordNetLemmatizer()
    stopwords_en = set(stopwords.words('english'))
    stopwords_bm = set(malaya_text.get_stopwords())
    combined_stopwords = stopwords_en.union(stopwords_bm)

    sentilex_dict = {}
    try:
        with open("SentiLexM.txt", 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 2:
                    word, polarity = parts
                    try:
                        sentilex_dict[word] = int(polarity)
                    except ValueError:
                        continue
    except FileNotFoundError:
        st.error("Error: 'SentiLexM.txt' not found. Please ensure it's in the same directory.")
    except Exception as e:
        st.error(f"Error loading SentiLexM.txt: {e}")

    vectorizer, model, label_encoder = None, None, None
    try:
        vectorizer = pickle.load(open("tfidf_vectorizer.pkl", "rb"))
        model = pickle.load(open("xgb_model.pkl", "rb"))
        label_encoder = pickle.load(open("label_encoder.pkl", "rb"))
    except FileNotFoundError:
        st.error("Error: One or more pickled model files (tfidf_vectorizer.pkl, xgb_model.pkl, label_encoder.pkl) not found. Please ensure they are in the same directory.")
    except Exception as e:
        st.error(f"Error loading pickled models: {e}")

    return lemmatizer, combined_stopwords, sentilex_dict, vectorizer, model, label_encoder

lemmatizer, combined_stopwords, sentilex_dict, vectorizer, model, label_encoder = load_nlp_assets()

# Reddit API setup - Consider using st.secrets for client_id and client_secret
reddit = praw.Reddit(
    client_id='g8XvUMIgaIR_jgvXOKvRRA', # Ideally, use st.secrets["reddit_client_id"]
    client_secret='R0GyitG39ekhlWVtNvKIH8I_NKX1eg', # Ideally, use st.secrets["reddit_client_secret"]
    user_agent='research/1.0'
)

subreddits = ["malaysia", "Bolehland"]
queries = [
    "MRT women coach", "KTM women coach", "LRT women coach",
    "MRT koc wanita", "KTM koc wanita", "LRT koc wanita"
]

def strip_malay_suffix(word):
    suffixes = ['nya', 'lah', 'pun', 'ku', 'mu', 'kah', 'tah', 'kan', 'an']
    for suffix in suffixes:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[:-len(suffix)]
    return word

def get_wordnet_pos(tag):
    if tag.startswith('J'): return wordnet.ADJ
    elif tag.startswith('V'): return wordnet.VERB
    elif tag.startswith('N'): return wordnet.NOUN
    elif tag.startswith('R'): return wordnet.ADV
    else: return wordnet.NOUN

def preprocess_text(text, lemmatizer_obj, combined_stopwords_set):
    text = str(text)
    text = contractions.fix(text)
    text = emoji.replace_emoji(text, replace='')
    text = re.sub(r"https?:\S+|www\.\S+", '', text)
    text = re.sub(r"(.)\1{2,}", r"\1\1", text)
    text = re.sub(r"[^a-zA-Z\s]", "", text)
    text = re.sub(r"\s+", ' ', text).strip()
    tokens = word_tokenize(text.lower())
    tokens = [w for w in tokens if w.isalpha() and w not in combined_stopwords_set]
    tokens = [strip_malay_suffix(w) for w in tokens]
    tagged = pos_tag(tokens)
    tokens = [lemmatizer_obj.lemmatize(w, get_wordnet_pos(pos)) for w, pos in tagged]
    return ' '.join(tokens)

def score_sentiment(tokens, sentilex_dict_obj):
    return sum(sentilex_dict_obj.get(word, 0) for word in tokens)

def label_sentiment(score):
    if score > 0:
        return 'positive'
    elif score < 0:
        return 'negative'
    else:
        return 'neutral'

def summarize_topics(model, df_to_summarize, topic_col, n_words=10, n_docs=1):
    topic_info = []
    # Ensure topics are iterated over from the model's actual topics, excluding -1
    # Check if model has topics before iterating
    if not hasattr(model, 'get_topics') or not model.get_topics():
        return pd.DataFrame() # Return empty DataFrame if no topics

    for topic_id in sorted([t for t in model.get_topics().keys() if t != -1]):
        keywords = [w for w, _ in model.get_topic(topic_id)[:n_words]]
        samples = df_to_summarize[df_to_summarize[topic_col] == topic_id]['text'].head(n_docs).tolist()
        topic_info.append({
            'Topic ID': topic_id,
            'Top Keywords': ', '.join(keywords),
            'Sample Text': samples[0] if samples else ''
        })
    return pd.DataFrame(topic_info)

def generate_word_cloud(topic_model, topic_id):
    """
    Generates a word cloud for a given topic ID from a BERTopic model.
    """
    if topic_id == -1:
        return None, "Word cloud not applicable for outlier topic (-1)."

    # Get topic words and their probabilities/frequencies
    topic_words_and_probs = topic_model.get_topic(topic_id)

    # Convert to a dictionary of word frequencies for WordCloud
    words_dict = {word: prob for word, prob in topic_words_and_probs if prob > 0}

    if not words_dict:
        return None, f"No significant keywords found for Topic ID {topic_id} to generate a word cloud."

    wordcloud = WordCloud(
        width=800,
        height=400,
        background_color='white',
        collocations=False,
        min_font_size=10,
        max_words=100
    ).generate_from_frequencies(words_dict)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.imshow(wordcloud, interpolation='bilinear')
    ax.axis('off')
    ax.set_title(f'Word Cloud for Topic ID: {topic_id}', fontsize=16)

    return fig, None

# @st.cache_data is for data that does not change between runs.
# The `topic_model` and `df` will be modified and stored in session_state,
# so this function might not need to be cached if it's only called once per button click.
# However, if collecting Reddit data itself is slow and static, keeping it cached is fine.
@st.cache_data(ttl=3600)
def collect_reddit_data_cached():
    data = []
    collected_post_ids = set()
    status_text = st.empty()
    for subreddit in subreddits:
        status_text.info(f"📡 Scraping r/{subreddit}...")
        for query in queries:
            status_text.info(f"📡 Searching '{query}' in r/{subreddit}...")
            try:
                # Limit submissions per query to prevent excessive scraping and rate limits
                for submission in reddit.subreddit(subreddit).search(query, limit=20): # Reduced limit for faster testing
                    if submission.id in collected_post_ids:
                        continue
                    collected_post_ids.add(submission.id)
                    text = submission.title + " " + submission.selftext
                    data.append({
                        'subreddit': subreddit,
                        'text': text,
                        'created_utc': submission.created_utc
                    })
                    # Add a small delay, but be mindful of Streamlit's refresh if it's too long
                    # For demonstration, you might remove it or make it very small if you want to see quick results.
                    # time.sleep(0.1)
            except Exception as e:
                status_text.error(f"Error scraping Reddit (subreddit: {subreddit}, query: {query}): {e}")
                continue
    status_text.empty() # Clear the status message once scraping is done
    return pd.DataFrame(data)

# Initialize session state variables if they don't exist
if 'analysis_complete' not in st.session_state:
    st.session_state.analysis_complete = False
if 'df' not in st.session_state:
    st.session_state.df = pd.DataFrame()
if 'topic_model' not in st.session_state:
    st.session_state.topic_model = None
if 'coherence_score' not in st.session_state:
    st.session_state.coherence_score = 0.0
if 'texts_for_coherence' not in st.session_state:
    st.session_state.texts_for_coherence = []


# Streamlit UI
st.title("Reddit NLP Dashboard (Live Scraping + Topic & Sentiment)")

# The analysis button
if st.button("Extract Reddit Data and Run Analysis"):
    if None in [sentilex_dict, vectorizer, model, label_encoder]:
        st.error("Cannot proceed with analysis due to missing model or SentiLexM files. Please check the console for more details.")
        st.session_state.analysis_complete = False
    else:
        st.info("📡 Scraping Reddit...")
        st.session_state.df = collect_reddit_data_cached()
        st.success(f"✅ Collected {len(st.session_state.df)} Reddit entries")

        if st.session_state.df.empty:
            st.warning("No data collected from Reddit. Analysis cannot proceed.")
            st.session_state.analysis_complete = False
        else:
            st.info("🧹 Preprocessing Text...")
            st.session_state.df['text_clean'] = st.session_state.df['text'].apply(lambda x: preprocess_text(x, lemmatizer, combined_stopwords))
            st.success("✅ Text preprocessing complete!")

            st.info("🧠 Topic Modeling with BERTopic...")
            all_stopwords = list(combined_stopwords)
            custom_vectorizer = CountVectorizer(stop_words=all_stopwords)
            st.session_state.topic_model = BERTopic(language="multilingual", vectorizer_model=custom_vectorizer, verbose=False)

            if len(st.session_state.df['text_clean']) > 1:
                topics, probs = st.session_state.topic_model.fit_transform(st.session_state.df['text_clean'])
                st.session_state.df['topic'] = topics

                st.session_state.texts_for_coherence = [doc.split() for doc in st.session_state.df['text_clean']]
                dictionary = Dictionary(st.session_state.texts_for_coherence)
                corpus = [dictionary.doc2bow(text) for text in st.session_state.texts_for_coherence]
                topic_words = [[w for w, _ in st.session_state.topic_model.get_topic(tid)[:10]] for tid in st.session_state.topic_model.get_topics().keys() if tid != -1]

                if topic_words and st.session_state.texts_for_coherence:
                    cm = CoherenceModel(topics=topic_words, texts=st.session_state.texts_for_coherence, dictionary=dictionary, coherence='c_v')
                    st.session_state.coherence_score = cm.get_coherence()
                    st.success(f"✅ Topic modeling complete (Coherence Score: {st.session_state.coherence_score:.4f})")
                else:
                    st.session_state.coherence_score = 0.0
                    st.warning("Could not calculate coherence score (not enough topics or texts).")
                    st.info("No meaningful topics could be extracted or only one topic was found. This can happen with very small datasets or if text is highly homogenous.")
                    st.session_state.df['topic'] = -1
            else:
                st.warning("Not enough data to perform topic modeling.")
                st.session_state.df['topic'] = -1
                st.session_state.coherence_score = 0.0

            st.info("❤️ Sentiment Analysis...")
            st.session_state.df['tokens'] = st.session_state.df['text_clean'].apply(lambda x: x.split())
            st.session_state.df['sentilexm_score'] = st.session_state.df['tokens'].apply(lambda x: score_sentiment(x, sentilex_dict))
            st.session_state.df['sentilexm_label'] = st.session_state.df['sentilexm_score'].apply(label_sentiment)

            if vectorizer and model and label_encoder:
                X_vec = vectorizer.transform(st.session_state.df['text_clean'])
                y_pred = model.predict(X_vec)
                st.session_state.df['xgb_sentiment'] = label_encoder.inverse_transform(y_pred)
            else:
                st.warning("XGBoost sentiment analysis skipped due to missing model files.")
                st.session_state.df['xgb_sentiment'] = 'neutral'

            st.session_state.analysis_complete = True
            st.success("Analysis complete!")

# --- Display Results (only if analysis has been run) ---
if st.session_state.analysis_complete and not st.session_state.df.empty:
    st.subheader("📌 Topics and Top Keywords")
    # Check if 'topic' column exists and there are distinct topics beyond -1
    if 'topic' in st.session_state.df.columns and len(st.session_state.df['topic'].unique()) > 1:
        topic_summary = summarize_topics(st.session_state.topic_model, st.session_state.df, 'topic')
        if not topic_summary.empty:
            st.dataframe(topic_summary)
        else:
            st.info("No distinct topics found to summarize.")
    else:
        st.info("No distinct topics found to summarize.")

    st.subheader("☁️ Word Clouds for Topics")
    if 'topic' in st.session_state.df.columns and len(st.session_state.df['topic'].unique()) > 1:
        available_topics = sorted([t for t in st.session_state.df['topic'].unique() if t != -1])
        if available_topics:
            selected_topic_wc = st.selectbox(
                "Select a Topic ID to view its Word Cloud:",
                options=available_topics,
                key="wordcloud_topic_selector" # Added a unique key
            )
            if selected_topic_wc is not None:
                fig_wc, msg_wc = generate_word_cloud(st.session_state.topic_model, selected_topic_wc)
                if fig_wc:
                    st.pyplot(fig_wc)
                    plt.close(fig_wc)
                else:
                    st.info(msg_wc)
            else:
                st.info("Please select a topic to generate a word cloud.")
        else:
            st.info("No distinct topics (excluding -1) found to generate word clouds.")
    else:
        st.info("Word clouds cannot be generated as no distinct topics were identified (e.g., only outlier topic -1 found).")

    st.subheader("📊 Average Topic Coherence Score")
    st.write(f"**{st.session_state.coherence_score:.4f}**")

    st.subheader("📈 Sentiment Distribution (Overall)")
    sentiment_counts = st.session_state.df['xgb_sentiment'].value_counts()
    st.bar_chart(sentiment_counts)

    st.subheader("📊 Sentiment Distribution by Topic")
    if 'topic' in st.session_state.df.columns and len(st.session_state.df['topic'].unique()) > 1:
        dist = st.session_state.df.groupby(['topic', 'xgb_sentiment']).size().unstack(fill_value=0)
        st.bar_chart(dist)
    else:
        st.info("No distinct topics for sentiment distribution by topic.")

    st.download_button(
        "⬇️ Download Results as CSV",
        st.session_state.df.to_csv(index=False).encode('utf-8'),
        "reddit_analysis_output.csv",
        "text/csv"
    )