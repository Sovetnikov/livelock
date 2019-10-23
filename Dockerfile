FROM python:3.7-alpine

RUN pip3 install sentry-sdk
RUN pip3 install livelock
EXPOSE 7873/tcp
COPY entrypoint.sh /entrypoint.sh
RUN sed -i -e 's/\r$//' /entrypoint.sh # Delete CR chars
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
