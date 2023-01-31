from airflow import DAG
import logging as log
import requests
import pendulum
import json
import csv
import pandas as pd 
from airflow.operators.python import PythonOperator
from airflow.operators.dummy import DummyOperator
from airflow.models import Variable
from airflow.models import TaskInstance
from google.cloud import storage
from databox import Client

def get_auth_header():
    my_bearer_token = Variable.get("TWITTER_BEARER_TOKEN")
# my_bearer_token = "AAAAAAAAAAAAAAAAAAAAAI8plgEAAAAAlEJ%2BFeKPvHQtEywKJSLeI2KOpMk%3D1d0PGWhFGwJqlhjxvgYD3ZGQzR4rzFIby09Uhk9dlbrMExxciV"
    return {"Authorization": f"Bearer {my_bearer_token}"}

def get_twitter_api_data_func(ti: TaskInstance, **kwargs):
    user_list = Variable.get(f"TWITTER_USER_IDS", [], deserialize_json=True)
    user_responses = []
    for user_id in user_list:
        api_url = f"https://api.twitter.com/2/users/{user_id}?user.fields=public_metrics,profile_image_url,username,description,id"
        request = requests.get(api_url, headers=get_auth_header())
        user_responses.append(request.json())
    ti.xcom_push("user requests", user_responses)

    tweet_list = Variable.get(f"TWITTER_TWEET_IDS", [], deserialize_json=True)
    tweet_responses = []
    for tweet_id in tweet_list:
        api_url = f"https://api.twitter.com/2/tweets/{tweet_id}?tweet.fields=public_metrics,author_id,text"
        request = requests.get(api_url, headers=get_auth_header())
        tweet_responses.append(request.json())
    ti.xcom_push("tweet requests", tweet_responses)

    log.info(user_responses)
    log.info(tweet_responses)


def transform_twitter_api_data_func(ti: TaskInstance, **kwargs):
    user_responses = ti.xcom_pull(key="user requests", task_ids="get_twitter_api_data_task")
    # user_id, username, name, followers_count, following_count, tweet_count, listed_count
    tweet_responses = ti.xcom_pull(key="tweet requests", task_ids="get_twitter_api_data_task")
    # tweet_id, text, retweet_count, reply_count, like_count, quote_count, impression count

    user_df = pd.DataFrame(columns=['user_id', 'username', 'name', 'followers_count', 'following_count', 'tweet_count', 'listed_count'])
    for item in user_responses:
        user_id = item['data']['id']
        username = item['data']['username']
        name = item['data']['name']
        followers = item['data']['public_metrics']['followers_count']
        following = item['data']['public_metrics']['following_count']
        tweet_count = item['data']['public_metrics']['tweet_count']
        listed_count = item['data']['public_metrics']['listed_count']

        user_df.loc[len(user_df.index)] = [user_id, username, name, followers, following, tweet_count, listed_count] 

    tweet_df = pd.DataFrame(columns=['tweet_id', 'text', 'retweet_count', 'reply_count', 'like_count', 'quote_count', 'impression_count'])
    for item in tweet_responses:
        tweet_id = item['data']['id']
        text = item['data']['text']
        retweets = item['data']['public_metrics']['retweet_count']
        replies = item['data']['public_metrics']['reply_count']
        likes = item['data']['public_metrics']['like_count']
        quotes = item['data']['public_metrics']['quote_count']
        impressions = item['data']['public_metrics']['impression_count']

        tweet_df.loc[len(tweet_df.index)] = [tweet_id, text, retweets, replies, likes, quotes, impressions]

    client = storage.Client()
    bucket = client.get_bucket("h-w-apache-airflow-cs280")
    bucket.blob("data/user_data.csv").upload_from_string(user_df.to_csv(index=False), "text/csv")
    bucket.blob("data/tweet_data.csv").upload_from_string(tweet_df.to_csv(index=False), "text/csv")

def upload_data_to_databox_func():
    databox_client = Client(Variable.get("DATABOX_TOKEN"))
    google_client = storage.Client()
    
    bucket = google_client.get_bucket("h-w-apache-airflow-cs280")
    user_blob = bucket.blob('data/user_data.csv')
    tweet_blob = bucket.blob('data/tweet_data')
    user_file = user_blob.download_to_filename('data/user_data.csv')
    tweet_file = tweet_blob.download_to_filename('data/tweet_data.csv')

    user_df = pd.read_csv(user_file)
    for row in user_df.iterrows():
        name = row['name']
        databox_client.push(f"{name}_followers_count", row['followers_count'])
        databox_client.push(f"{name}_following_count", row['following_count'])
        databox_client.push(f"{name}_tweet_count", row['tweet_count'])
        databox_client.push(f"{name}_listed_count", row['listed_count'])

    tweet_df = pd.read_csv(tweet_file)
    for row in tweet_df.iterrows():
        databox_client.push("reply_count", row['reply_count'])
        databox_client.push("like_count", row['like_count'])
        databox_client.push("impression_count", row['impression_count'])
        databox_client.push("retweet_count", row['retweet_count'])


with DAG(
    dag_id="project_lab_1_etl",
    schedule_interval="0 9 * * *",
    start_date=pendulum.datetime(2023, 1, 1, tz="US/Pacific"),
    catchup=False,
) as dag:
    start_task = DummyOperator(task_id="start_task")
    first_task = PythonOperator(task_id="get_twitter_api_data_task", python_callable=get_twitter_api_data_func, provide_context=True)
    second_task = PythonOperator(task_id="transform_twitter_api_data_task", python_callable=transform_twitter_api_data_func, provide_context=True)
    third_task = PythonOperator(task_id="upload_data_to_databox_task", python_callable=upload_data_to_databox_func)
    end_task = DummyOperator(task_id="end_task")

start_task >> first_task >> second_task >> third_task >> end_task