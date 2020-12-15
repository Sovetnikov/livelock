FROM python:3.7

RUN pip3 install livelock
RUN pip3 install sentry-sdk==0.14.0
RUN pip3 install prometheus-client==0.7.1
EXPOSE 7873/tcp
COPY entrypoint.sh /entrypoint.sh
RUN sed -i -e 's/\r$//' /entrypoint.sh # Delete CR chars
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
