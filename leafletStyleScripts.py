import os
import shutil
import re
from math import floor
import xml.etree.ElementTree
from PyQt4.QtCore import QPyNullVariant
from qgis.core import (QgsSingleSymbolRendererV2,
                       QgsCategorizedSymbolRendererV2,
                       QgsGraduatedSymbolRendererV2,
                       QgsRuleBasedRendererV2,
                       QgsSimpleMarkerSymbolLayerV2,
                       QgsSimpleLineSymbolLayerV2,
                       QgsSimpleFillSymbolLayerV2,
                       QgsSvgMarkerSymbolLayerV2)
from exp2js import compile_to_file
from utils import getRGBAColor, handleHiddenField


def getLayerStyle(layer, sln, markerFolder, outputProjectFilename):
    markerType = None
    renderer = layer.rendererV2()
    layer_alpha = layer.layerTransparency()
    style = ""
    if isinstance(renderer, QgsSingleSymbolRendererV2):
        style = ""
        symbol = renderer.symbol()
        for i in xrange(symbol.symbolLayerCount()):
            (styleCode, markerType) = getSymbolAsStyle(symbol, markerFolder,
                                                       layer_alpha, sln, i)
            style += """
        function style_%s_%s() {
            return %s
        }""" % (sln, i, styleCode)
            
    elif isinstance(renderer, QgsCategorizedSymbolRendererV2):
        classAttr = handleHiddenField(layer, renderer.classAttribute())
        style = """
        function style_%s(feature) {
            switch(feature.properties['%s']) {""" % (sln, classAttr)
        for cat in renderer.categories():
            styleCodes = ""
            for i in xrange(cat.symbol().symbolLayerCount()):
                (styleCode, markerType) = getSymbolAsStyle(cat.symbol(),
                                                       markerFolder,
                                                       layer_alpha, sln, i)
                styleCodes += styleCode
                if (cat.value() is not None and cat.value() != "" and
                        not isinstance(cat.value(), QPyNullVariant)):
                    style += """
                    case '%s':""" % unicode(cat.value()).replace("'", "\\'")
                else:
                    style += """
                    default:"""
                style += """
                        return %s
                        break;""" % styleCodes
            style += """
            }
        }"""
    elif isinstance(renderer, QgsGraduatedSymbolRendererV2):
        classAttr = handleHiddenField(layer, renderer.classAttribute())
        style = """
        function style_%s(feature) {""" % (sln)
        for ran in renderer.ranges():
            (styleCode, markerType) = getSymbolAsStyle(ran.symbol(),
                                                       markerFolder,
                                                       layer_alpha, sln)
            style += """
            if (feature.properties['%(a)s'] >= %(l)f """
            style += """&& feature.properties['%(a)s'] <= %(u)f ) {
                return %(s)s
            }"""
            style = style % {"a": classAttr, "l": ran.lowerValue(),
                             "u": ran.upperValue(),
                             "s": styleCode}
        style += """
        }"""
    elif isinstance(renderer, QgsRuleBasedRendererV2):
        template = """
        function style_%s(feature) {
            var context = {
                feature: feature,
                variables: {}
            };
            // Start of if blocks and style check logic
            %s
            else {
                return %s;
            }
        }
        """
        elsejs = "{fill: false, stroke: false}"
        js = ""
        root_rule = renderer.rootRule()
        rules = root_rule.children()
        expFile = os.path.join(outputProjectFilename, "js",
                               "qgis2web_expressions.js")
        ifelse = "if"
        for count, rule in enumerate(rules):
            (styleCode, markerType) = getSymbolAsStyle(rule.symbol(),
                                                       markerFolder,
                                                       layer_alpha, sln)
            name = "".join((sln, "rule", unicode(count)))
            exp = rule.filterExpression()
            if rule.isElse():
                elsejs = styleCode
                continue
            name = compile_to_file(exp, name, "Leaflet", expFile)
            js += """
            %s (%s(context)) {
              return %s;
            }
            """ % (ifelse, name, styleCode)
            js = js.strip()
            ifelse = "else if"
        style = template % (sln, js, elsejs)
    else:
        style = ""
    return style, markerType


def getSymbolAsStyle(symbol, markerFolder, layer_transparency, sln, i):
    markerType = None
    styles = []
    if layer_transparency == 0:
        alpha = symbol.alpha()
    else:
        alpha = 1-(layer_transparency / float(100))
    sl = symbol.symbolLayer(i)
    props = sl.properties()
    if isinstance(sl, QgsSimpleMarkerSymbolLayerV2):
        color = getRGBAColor(props["color"], alpha)
        borderColor = getRGBAColor(props["outline_color"], alpha)
        borderWidth = props["outline_width"]
        lineStyle = props["outline_style"]
        size = sl.size() * 2
        style = getCircle(color, borderColor, borderWidth,
                          size, props, lineStyle)
        markerType = "circleMarker"
    elif isinstance(sl, QgsSvgMarkerSymbolLayerV2):
        path = os.path.join(markerFolder, os.path.basename(sl.path()))
        svgSize = sl.size() * 3.8
        if symbol.dataDefinedAngle().isActive():
            if symbol.dataDefinedAngle().useExpression():
                rot = "0"
            else:
                rot = "feature.get("
                rot += symbol.dataDefinedAngle().expressionOrField()
                rot += ") * 0.0174533"
        else:
            rot = unicode(sl.angle() * 0.0174533)
        shutil.copy(sl.path(), path)
        style = """
        rotationAngle: %s,
        rotationOrigin: 'center center',
        icon: %s""" % (rot, getIcon("markers/" + os.path.basename(sl.path()),
                                    svgSize))
        markerType = "marker"
    elif isinstance(sl, QgsSimpleLineSymbolLayerV2):

        # Check for old version
        if 'color' in props:
            color = getRGBAColor(props["color"], alpha)
        else:
            color = getRGBAColor(props["line_color"], alpha)

        if 'width' in props:
            line_width = props["width"]
        else:
            line_width = props["line_width"]

        if 'penstyle' in props:
            line_style = props["penstyle"]
        else:
            line_style = props["line_style"]

        lineCap = sl.penCapStyle()
        lineJoin = sl.penJoinStyle()

        style = getStrokeStyle(color, line_style, line_width,
                               lineCap, lineJoin)
        style += """
                fillOpacity: 0,"""
    elif isinstance(sl, QgsSimpleFillSymbolLayerV2):
        fillColor = getRGBAColor(props["color"], alpha)

        # for old version
        if 'color_border' in props:
            borderColor = getRGBAColor(props["color_border"], alpha)
        else:
            borderColor = getRGBAColor(props["outline_color"], alpha)

        if 'style_border' in props:
            borderStyle = props["style_border"]
        else:
            borderStyle = props["outline_style"]

        if 'width_border' in props:
            borderWidth = props["width_border"]
        else:
            borderWidth = props["outline_width"]

        try:
            lineCap = sl.penCapStyle()
            lineJoin = sl.penJoinStyle()
        except:
            lineCap = 0
            lineJoin = 0

        style = ('''%s %s''' %
                 (getStrokeStyle(borderColor, borderStyle, borderWidth,
                                 lineCap, lineJoin),
                  getFillStyle(fillColor, props)))
    else:
        style = ""
    return ("""{
                pane: 'pane_%s',%s
            }""" % (sln, style), markerType)


def getCircle(color, borderColor, borderWidth, size, props, lineStyle):
    return ("""
                radius: %s,%s%s""" %
            (size,
             getStrokeStyle(borderColor, lineStyle, borderWidth, 0, 0),
             getFillStyle(color, props)))


def getIcon(path, svgSize):
    return '''L.icon({
            iconUrl: '%(path)s',
            iconSize: [%(s)s, %(s)s]
        }),''' % {"s": svgSize, "path": path.replace("\\", "\\\\")}


def getStrokeStyle(color, dashed, width, linecap, linejoin):
    width = round(float(width) * 3.8, 0)
    if width == 0 and dashed != "no":
        width = 1
    dash = dashed.replace("dash", "10,5")
    dash = dash.replace("dot", "1,5")
    dash = dash.replace("solid", "")
    dash = dash.replace(" ", ",")
    if dash == "no":
        dash = ""
    capString = "round"
    if linecap == 0:
        capString = "butt"
    if linecap == 16:
        capString = "square"
    joinString = "round"
    if linejoin == 0:
        joinString = "miter"
    if linejoin == 64:
        joinString = "bevel"
    strokeString = ("""
                opacity: 1,
                color: %s,
                dashArray: '%s',""" %
                    (color, dash))
    strokeString += ("""
                lineCap: '%s',
                lineJoin: '%s',
                weight: %s,""" %
                     (capString, joinString, width))
    return strokeString


def getFillStyle(color, props):
    try:
        if props["style"] == "no":
            return """
                fillOpacity: 0,"""
    except:
        pass
    return """
                fillOpacity: 1,
                fillColor: %s,""" % color
