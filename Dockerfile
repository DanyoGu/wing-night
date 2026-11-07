FROM python:3.13-slim
WORKDIR /app
COPY server.py ./
COPY dist ./dist
ARG WING_REVISION=development
ARG WING_COMMIT_COUNT=0
RUN python -c "import json,os,re; sha=os.environ['WING_REVISION']; count=int(os.environ['WING_COMMIT_COUNT']); assert count>=0 and (sha=='development' or re.fullmatch('[0-9a-f]{40}',sha)); open('release.json','w').write(json.dumps({'version':'v0.'+str(count),'revision':sha,'commit_count':count}))"
RUN python -c "import json,pathlib; r=json.load(open('release.json')); p=pathlib.Path('dist/index.html'); html=p.read_text(); old='data-revision=\"development\" data-version=\"v0.0\"'; assert old in html; p.write_text(html.replace(old,'data-revision=\"'+r['revision']+'\" data-version=\"'+r['version']+'\"'))"
LABEL org.opencontainers.image.revision="${WING_REVISION}" work.wing-night.version="v0.${WING_COMMIT_COUNT}"
RUN mkdir /data && useradd --uid 10001 --create-home wing && chown wing:wing /data
USER wing
ENV PORT=8040 WING_DB=/data/wing-night.sqlite PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8040
CMD ["python", "server.py"]
