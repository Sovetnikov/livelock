FROM python:3.7-alpine

RUN pip3 install sentry-sdk
RUN pip3 install --upgrade https://github.com/Sovetnikov/livelock/archive/master.zip?2
EXPOSE 7873/tcp
COPY entrypoint.sh /entrypoint.sh
RUN sed -i -e 's/\r$//' /entrypoint.sh # Delete CR chars
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
