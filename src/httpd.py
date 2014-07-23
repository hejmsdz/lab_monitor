from flask import *

from database import *
from sensors import SSHiLoSensors
from controller import ILoController

import ssehandler
import gevent
from gevent.wsgi import WSGIServer


servers_dao, sensors_dao = ServersDAO(), SensorsDAO()

app = Flask(__name__)
app.debug = True
app.jinja_env.filters['unsafejson'] = lambda v: json.dumps(v)
controller_inst = None
controller_gevent = None
handler = ssehandler.SSEHandler()

@app.route('/')
def dashboard():
    servers = servers_dao.server_list()
    return render_template('dashboard.html', servers=servers)


@app.route('/status')
def status0():
    try:
        server = servers_dao.server_list()[0]['addr']
        return redirect(url_for('status', server=server))
    except IndexError:
        # no servers defined
        return redirect(url_for('config_servers'))

@app.route('/status/<server>')
def status(server):
    return redirect(url_for('status_temperature', server=server))

@app.route('/status/<server>/temperature')
def status_temperature(server):
    servers = servers_dao.server_list()
    return render_template('status.html', servers=servers, server=server, data_src='json_temperature')

@app.route('/status/<server>/power_usage')
def status_power_usage(server):
    servers = servers_dao.server_list()
    return render_template('status.html', servers=servers, server=server, data_src='json_power_usage')

@app.route('/status/<server>/status')
def status_status(server):
    servers = servers_dao.server_list()
    return render_template('status.html', servers=servers, server=server, data_src='json_status')

@app.route('/status/<server>/power_units')
def status_power_units(server):
    servers = servers_dao.server_list()
    return render_template('status.html', servers=servers, server=server, data_src='json_power_units')


@app.route('/config')
def config():
    return redirect(url_for('config_servers'))

@app.route('/config/servers')
def config_servers():
    servers = servers_dao.server_list()
    return render_template('config_servers.html', servers=servers)

@app.route('/config/servers/create', methods=['POST'])
def config_servers_create():
    try:
        addr = request.form['addr']
        type_ = request.form['type']
        rack = int(request.form['rack'])
        size = int(request.form['size'])
        position = int(request.form['position'])

        # are numbers OK?
        if rack not in xrange(7):
            raise ValueError("Rack number must be between 0 and 6")
        if size not in xrange(1,6):
            raise ValueError("Size must be between 1 and 5")
        if position not in xrange(1,43):
            raise ValueError("Position must be between 1 and 42")

        # is anyone trying to put a 3U server on the 42nd position?
        if position+size>43:
            raise ValueError("Server does not fit")

        # are there any other servers on this place?
        if servers_dao.server_position(rack, position, position+size-1):
            raise ValueError("There is a server on this place")

        # is this host reachable?
        # (it takes the most time, so it's better to check other conditions first)
        sensor = SSHiLoSensors(addr)
        sensor.disconnect()

        servers_dao.server_create(addr, type_, rack, size, position)
        controller_restart()
        return redirect(url_for('config_servers'))

    except BaseException as e:
        return jsonify(error=str(e))

@app.route('/config/servers/update/', methods=['POST'])
def config_servers_update():
    try:
        addr = request.form['addr']
        type_ = request.form['type']
        rack = int(request.form['rack'])
        size = int(request.form['size'])
        position = int(request.form['position'])

        # are numbers OK?
        if rack not in xrange(7):
            raise ValueError("Rack number must be between 0 and 6")
        if size not in xrange(1,6):
            raise ValueError("Size must be between 1 and 5")
        if position not in xrange(1,43):
            raise ValueError("Position must be between 1 and 42")

        # is anyone trying to put a 3U server on the 42nd position?
        if position+size>43:
            raise ValueError("Server does not fit")

        # are there any other servers on this place?
        if servers_dao.server_position(rack, position, position+size-1, addr):
            raise ValueError("There is a server on this place")


        servers_dao.server_update(addr=addr, update={'type_':type_, 'rack':rack, 'size':size, 'position':position})
        controller_restart()
        return redirect(url_for('config_servers'))

    except BaseException as e:
        return jsonify(error=str(e))

@app.route('/config/servers/delete/<server>')
def config_servers_delete(server):
    try:
        servers_dao.server_delete(addr=server)
        controller_restart()
        return redirect(url_for('config_servers'))

    except BaseException as e:
        return jsonify(error=str(e))


@app.route('/controller')
def controller():
    servers = servers_dao.server_list()
    return render_template('controller.html', servers=servers)

@app.route("/controller/stream")
def controller_stream():
    return Response(handler.subscribe(), mimetype="text/event-stream")

@app.route("/controller/start")
def controller_start():
    global controller_gevent

    def start():
        global controller_inst
        global handler
        controller_inst = ILoController([handler])
        controller_inst.main_loop()

    if controller_inst is None:
        controller_gevent = gevent.spawn(start)
    return "Going"

@app.route("/controller/stop")
def controller_stop():
    global controller_inst
    if controller_inst is not None:
        controller_inst.stop()

        def stop():
            global controller_gevent
            global controller_inst
            controller_gevent.join()
            controller_inst = None

        gevent.spawn(stop)

    return "Stopped"

@app.route("/controller/restart")
def controller_restart():
    global controller_inst
    global controller_gevent
    if controller_inst is not None:
        controller_inst.stop()

        def restart():
            global controller_gevent

            def stop():
                global controller_gevent
                global controller_inst
                controller_gevent.join()
                controller_inst = None

            def start():
                global controller_inst
                global handler
                controller_inst = ILoController([handler])
                controller_inst.main_loop()

            gevent.spawn(stop).join()
            controller_gevent = gevent.spawn(start)

        gevent.spawn(restart)

    return "Restarting"

@app.route("/controller/status")
def controller_status():
    global controller_inst
    if controller_inst is not None:
        return controller_inst.state
    else:
        return "not started"


@app.route('/shutdown')
def shutdown():
    return ''


@app.route('/json/servers')
def json_servers():
    servers = servers_dao.server_list(with_health=True)
    return jsonify(servers=servers)

@app.route('/json/server/<server>/temperature')
def json_temperature(server):
    start = request.args.get('start', None)
    end = request.args.get('end', None)
    data = sensors_dao.get_temperature(server, start, end)
    return jsonify(**data)


@app.route('/json/server/<server>/power_usage')
def json_power_usage(server):
    start = request.args.get('start', None)
    end = request.args.get('end', None)
    data = sensors_dao.get_power_usage(server, start, end)
    return jsonify(**data)

@app.route('/json/server/<server>/power_units')
def json_power_units(server):
    start = request.args.get('start', None)
    end = request.args.get('end', None)
    data = sensors_dao.get_power_units(server, start, end)
    return jsonify(**data)

@app.route('/json/server/<server>/status')
def json_status(server):
    start = request.args.get('start', None)
    end = request.args.get('end', None)
    data = sensors_dao.get_status(server, start, end)
    return jsonify(**data)

if __name__ == "__main__":
    app.debug = True
    server = WSGIServer(("", 5000), app)
    server.serve_forever()


#if __name__ == '__main__':
#    app.run(host='0.0.0.0')