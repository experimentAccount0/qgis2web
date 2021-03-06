import os
import shutil
import re
import codecs
import math
import xml.etree.ElementTree
import traceback
from PyQt4.QtCore import QDir, QPyNullVariant, QSize
from qgis.core import (QgsVectorLayer,
                       QgsSingleSymbolRendererV2,
                       QgsCategorizedSymbolRendererV2,
                       QgsGraduatedSymbolRendererV2,
                       QgsRuleBasedRendererV2,
                       QgsHeatmapRenderer,
                       QgsSimpleMarkerSymbolLayerV2,
                       QgsSvgMarkerSymbolLayerV2,
                       QgsSimpleLineSymbolLayerV2,
                       QgsSimpleFillSymbolLayerV2,
                       QgsSymbolLayerV2Utils,
                       QgsPalLayerSettings,
                       QgsMessageLog)
from exp2js import compile_to_file
from utils import safeName, getRGBAColor


def exportStyles(layers, folder, clustered):
    stylesFolder = os.path.join(folder, "styles")
    QDir().mkpath(stylesFolder)
    legendFolder = os.path.join(stylesFolder, "legend")
    QDir().mkpath(legendFolder)
    for count, (layer, cluster) in enumerate(zip(layers, clustered)):
        sln = safeName(layer.name()) + unicode(count)
        if layer.type() != layer.VectorLayer:
            continue
        labelsEnabled = unicode(
            layer.customProperty("labeling/enabled")).lower() == "true"
        if (labelsEnabled):
            labelField = layer.customProperty("labeling/fieldName")
            if labelField != "":
                if unicode(layer.customProperty(
                        "labeling/isExpression")).lower() == "true":
                    exprFilename = os.path.join(folder, "resources",
                                                "qgis2web_expressions.js")
                    fieldName = layer.customProperty("labeling/fieldName")
                    name = compile_to_file(fieldName, "label_%s" % sln,
                                           "OpenLayers3", exprFilename)
                    js = "%s(context)" % (name)
                    js = js.strip()
                    labelText = js
                else:
                    fieldIndex = layer.pendingFields().indexFromName(
                        labelField)
                    editFormConfig = layer.editFormConfig()
                    editorWidget = editFormConfig.widgetType(fieldIndex)
                    if (editorWidget == QgsVectorLayer.Hidden or
                            editorWidget == 'Hidden'):
                        labelField = "q2wHide_" + labelField
                    labelText = ('feature.get("%s")' %
                                 labelField.replace('"', '\\"'))
            else:
                labelText = '""'
        else:
            labelText = '""'
        defs = "var size = 0;\n"
        try:
            renderer = layer.rendererV2()
            layer_alpha = layer.layerTransparency()
            if isinstance(renderer, QgsSingleSymbolRendererV2):
                symbol = renderer.symbol()
                style = "var style = " + getSymbolAsStyle(symbol,
                                                          stylesFolder,
                                                          layer_alpha,
                                                          renderer)
                legendIcon = QgsSymbolLayerV2Utils.symbolPreviewPixmap(
                    symbol, QSize(16, 16))
                legendIcon.save(os.path.join(legendFolder, sln + ".png"))
                value = 'var value = ""'
            elif isinstance(renderer, QgsCategorizedSymbolRendererV2):
                cluster = False
                defs += """function categories_%s(feature, value, size) {
                switch(value) {""" % sln
                cats = []
                for cnt, cat in enumerate(renderer.categories()):
                    legendIcon = QgsSymbolLayerV2Utils.symbolPreviewPixmap(
                        cat.symbol(), QSize(16, 16))
                    legendIcon.save(os.path.join(
                        legendFolder, sln + "_" + unicode(cnt) + ".png"))
                    if (cat.value() is not None and cat.value() != "" and
                            not isinstance(cat.value(), QPyNullVariant)):
                        categoryStr = "case '%s':" % unicode(
                            cat.value()).replace("'", "\\'")
                    else:
                        categoryStr = "default:"
                    categoryStr += '''
                    return %s;
                    break;''' % (getSymbolAsStyle(cat.symbol(), stylesFolder,
                                                  layer_alpha, renderer))
                    cats.append(categoryStr)
                defs += "\n".join(cats) + "}};"
                classAttr = renderer.classAttribute()
                fieldIndex = layer.pendingFields().indexFromName(classAttr)
                editFormConfig = layer.editFormConfig()
                editorWidget = editFormConfig.widgetType(fieldIndex)
                if (editorWidget == QgsVectorLayer.Hidden or
                        editorWidget == 'Hidden'):
                    classAttr = "q2wHide_" + classAttr
                value = ('var value = feature.get("%s");' % classAttr)
                style = (
                    'var style = categories_%s(feature, value, size)' % sln)
            elif isinstance(renderer, QgsGraduatedSymbolRendererV2):
                cluster = False
                varName = "ranges_" + sln
                defs += "var %s = [" % varName
                ranges = []
                for cnt, ran in enumerate(renderer.ranges()):
                    legendIcon = QgsSymbolLayerV2Utils.symbolPreviewPixmap(
                        ran.symbol(), QSize(16, 16))
                    legendIcon.save(os.path.join(
                        legendFolder, sln + "_" + unicode(cnt) + ".png"))
                    symbolstyle = getSymbolAsStyle(ran.symbol(), stylesFolder,
                                                   layer_alpha, renderer)
                    ranges.append('[%f, %f, %s]' % (ran.lowerValue(),
                                                    ran.upperValue(),
                                                    symbolstyle))
                defs += ",\n".join(ranges) + "];"
                classAttr = renderer.classAttribute()
                fieldIndex = layer.pendingFields().indexFromName(classAttr)
                editFormConfig = layer.editFormConfig()
                editorWidget = editFormConfig.widgetType(fieldIndex)
                if (editorWidget == QgsVectorLayer.Hidden or
                        editorWidget == 'Hidden'):
                    classAttr = "q2wHide_" + classAttr
                value = ('var value = feature.get("%s");' % classAttr)
                style = '''var style = %(v)s[0][2];
    for (i = 0; i < %(v)s.length; i++){
        var range = %(v)s[i];
        if (value > range[0] && value<=range[1]){
            style =  range[2];
        }
    }''' % {"v": varName}
            elif isinstance(renderer, QgsRuleBasedRendererV2):
                cluster = False
                template = """
        function rules_%s(feature, value) {
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
        var style = rules_%s(feature, value);
        """
                elsejs = "[]"
                js = ""
                root_rule = renderer.rootRule()
                rules = root_rule.children()
                expFile = os.path.join(folder, "resources",
                                       "qgis2web_expressions.js")
                ifelse = "if"
                for count, rule in enumerate(rules):
                    symbol = rule.symbol()
                    styleCode = getSymbolAsStyle(symbol, stylesFolder,
                                                 layer_alpha, renderer)
                    name = "".join((sln, "rule", unicode(count)))
                    exp = rule.filterExpression()
                    if rule.isElse():
                        elsejs = styleCode
                        continue
                    name = compile_to_file(exp, name, "OpenLayers3", expFile)
                    js += """
                    %s (%s(context)) {
                      return %s;
                    }
                    """ % (ifelse, name, styleCode)
                    js = js.strip()
                    ifelse = "else if"
                value = ("var value = '';")
                style = template % (sln, js, elsejs, sln)
            else:
                style = ""
            if layer.customProperty("labeling/fontSize"):
                size = float(layer.customProperty("labeling/fontSize")) * 1.3
            else:
                size = 10
            italic = layer.customProperty("labeling/fontItalic")
            bold = layer.customProperty("labeling/fontWeight")
            r = layer.customProperty("labeling/textColorR")
            g = layer.customProperty("labeling/textColorG")
            b = layer.customProperty("labeling/textColorB")
            if (r or g or b) is None:
                color = "rgba(0, 0, 0, 1)"
            else:
                color = "rgba(%s, %s, %s, 1)" % (r, g, b)
            face = layer.customProperty("labeling/fontFamily")
            if face is None:
                face = ","
            else:
                face = " \\'%s\\'," % face
            palyr = QgsPalLayerSettings()
            palyr.readFromLayer(layer)
            sv = palyr.scaleVisibility
            if sv:
                min = float(palyr.scaleMin)
                max = float(palyr.scaleMax)
                if min != 0:
                    min = 1 / ((1 / min) * 39.37 * 90.7)
                max = 1 / ((1 / max) * 39.37 * 90.7)
                labelRes = " && resolution > %(min)d " % {"min": min}
                labelRes += "&& resolution < %(max)d" % {"max": max}
            else:
                labelRes = ""
            buffer = palyr.bufferDraw
            if buffer:
                bufferColor = palyr.bufferColor.name()
                bufferWidth = palyr.bufferSize
                stroke = """
              stroke: new ol.style.Stroke({
                color: "%s",
                width: %d
              }),""" % (bufferColor, bufferWidth)
            else:
                stroke = ""
            if style != "":
                style = getStyle(style, cluster, labelRes, labelText,
                                 sln, size, face, color, value)
            else:
                style = "''"
        except Exception, e:
            style = """{
            /* """ + traceback.format_exc() + " */}"
            QgsMessageLog.logMessage(traceback.format_exc(), "qgis2web",
                                     level=QgsMessageLog.CRITICAL)

        path = os.path.join(stylesFolder, sln + "_style.js")

        with codecs.open(path, "w", "utf-8") as f:
            f.write('''%(defs)s
var styleCache_%(name)s={}
var style_%(name)s = %(style)s;''' %
                    {"defs": defs, "name": sln, "style": style})


def getStyle(style, cluster, labelRes, labelText,
             sln, size, face, color, value):
    this_style = '''function(feature, resolution){
    var context = {
        feature: feature,
        variables: {}
    };
    %(value)s
    var labelText = "";
    var key = "";
    ''' % {
        "value": value}
    if cluster:
        this_style += '''var clusteredFeatures = feature.get("features");
    size = clusteredFeatures.length;
    var textAlign = "center";
    var offsetX = 0;
    var offsetY = 0;
    if (size == 1) {
        textAlign = "left"
        offsetX = 8
        offsetY = 3
        var feature = clusteredFeatures[0];
        if (%(label)s !== null%(labelRes)s) {
            labelText = String(%(label)s);
        } else {
            labelText = ""
        }
        key = value + "_" + labelText
    } else {
        labelText = size.toString()
        size = 2*(Math.log(size)/ Math.log(2))
    }
    %(style)s;\n''' % {
            "style": style, "labelRes": labelRes, "label": labelText}
    else:
        this_style += '''size = 0;
    var textAlign = "left";
    var offsetX = 8;
    var offsetY = 3;
    if (%(label)s !== null%(labelRes)s) {
        labelText = String(%(label)s);
    } else {
        labelText = ""
    }
    %(style)s;\n''' % {
            "style": style, "labelRes": labelRes, "label": labelText}

    this_style += '''    key = value + "_" + labelText
    if (!%(cache)s[key]){
        var text = new ol.style.Text({
                font: '%(size)spx%(face)s sans-serif',
                text: labelText,
                textBaseline: "middle",
                textAlign: textAlign,
                offsetX: offsetX,
                offsetY: offsetY,
                fill: new ol.style.Fill({
                  color: '%(color)s'
                })
            });
        %(cache)s[key] = new ol.style.Style({"text": text})
    }
    var allStyles = [%(cache)s[key]];
    allStyles.push.apply(allStyles, style);
    return allStyles;
}''' % {
            "cache": "styleCache_" + sln,
            "size": size, "face": face,
            "color": color}
    return this_style


def getSymbolAsStyle(symbol, stylesFolder, layer_transparency, renderer):
    styles = {}
    if layer_transparency == 0:
        alpha = symbol.alpha()
    else:
        alpha = 1 - (layer_transparency / float(100))
    for i in xrange(symbol.symbolLayerCount()):
        sl = symbol.symbolLayer(i)
        props = sl.properties()
        if isinstance(sl, QgsSimpleMarkerSymbolLayerV2):
            color = getRGBAColor(props["color"], alpha)
            borderColor = getRGBAColor(props["outline_color"], alpha)
            borderWidth = props["outline_width"]
            size = sl.size() * 2
            try:
                if sl.shape() == 0:
                    style = "image: %s" % getSquare(color, borderColor,
                                                    borderWidth, size, props)
                elif sl.shape() == 1:
                    style = "image: %s" % getDiamond(color, borderColor,
                                                     borderWidth, size, props)
                elif sl.shape() == 2:
                    style = "image: %s" % getPentagon(color, borderColor,
                                                      borderWidth, size, props)
                elif sl.shape() == 3:
                    style = "image: %s" % getHexagon(color, borderColor,
                                                     borderWidth, size, props)
                elif sl.shape() == 4 or sl.shape() == 5:
                    style = "image: %s" % getTriangle(color, borderColor,
                                                      borderWidth, size, props)
                elif sl.shape() == 6:
                    style = "image: %s" % getStar(color, borderColor,
                                                  borderWidth, size, props)
                elif sl.shape() == 9:
                    style = "image: %s" % getCross(color, borderColor,
                                                   borderWidth, size, props)
                elif sl.shape() == 11:
                    style = "image: %s" % getCross2(color, borderColor,
                                                    borderWidth, size, props)
                else:
                    style = "image: %s" % getCircle(color, borderColor,
                                                    borderWidth, size, props)
            except:
                style = "image: %s" % getCircle(color, borderColor,
                                                borderWidth, size, props)
        elif isinstance(sl, QgsSvgMarkerSymbolLayerV2):
            path = os.path.join(stylesFolder, os.path.basename(sl.path()))
            svg = xml.etree.ElementTree.parse(sl.path()).getroot()
            svgWidth = svg.attrib["width"]
            svgWidth = re.sub("px", "", svgWidth)
            svgWidth = re.sub("mm", "", svgWidth)
            svgHeight = svg.attrib["height"]
            svgHeight = re.sub("px", "", svgHeight)
            svgHeight = re.sub("mm", "", svgHeight)
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
            style = ("image: %s" %
                     getIcon("styles/" + os.path.basename(sl.path()),
                             sl.size(), svgWidth, svgHeight, rot))
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
        if renderer.usingSymbolLevels():
            k = sl.renderingPass()
        else:
            k = i
        styles[k] = '''new ol.style.Style({
        %s
    })''' % style
    return "[ %s]" % ",".join(styles[s] for s in sorted(styles.iterkeys()))


def getSquare(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 4,
            angle: Math.PI/4, %s %s})""" % (size, stroke,
                                            getFillStyle(color, props)))


def getDiamond(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 4,
            %s %s})""" % (size, stroke, getFillStyle(color, props)))


def getPentagon(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 5,
            %s %s})""" % (size, stroke, getFillStyle(color, props)))


def getHexagon(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 6,
            %s %s})""" % (size, stroke, getFillStyle(color, props)))


def getTriangle(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 3,
            %s %s})""" % (size, stroke, getFillStyle(color, props)))


def getStar(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 5,
            radius2: %s, %s %s})""" % (size, size/2, stroke,
                                       getFillStyle(color, props)))


def getCircle(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.Circle({radius: %s + size,
            %s %s})""" % (size, stroke, getFillStyle(color, props)))


def getCross(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 4,
            radius2: 0, %s %s})""" % (size, stroke,
                                      getFillStyle(color, props)))


def getCross2(color, borderColor, borderWidth, size, props):
    if props['outline_style'] == "no":
        stroke = ""
    else:
        stroke = getStrokeStyle(borderColor, "", borderWidth, 0, 0)
    return ("""new ol.style.RegularShape({radius: %s + size, points: 4,
            radius2: 0, angle: Math.PI / 4, %s %s})""" % (
                size, stroke, getFillStyle(color, props)))


def getIcon(path, size, svgWidth, svgHeight, rot):
    size = math.floor(float(size) * 3.8)
    anchor = size / 2
    scale = unicode(float(size) / float(svgWidth))
    return '''new ol.style.Icon({
                  imgSize: [%(w)s, %(h)s],
                  scale: %(scale)s,
                  anchor: [%(a)d, %(a)d],
                  anchorXUnits: "pixels",
                  anchorYUnits: "pixels",
                  rotation: %(rot)s,
                  src: "%(path)s"
            })''' % {"w": svgWidth, "h": svgHeight,
                     "scale": scale, "rot": rot,
                     "s": size, "a": anchor,
                     "path": path.replace("\\", "\\\\")}


def getStrokeStyle(color, dashed, width, linecap, linejoin):
    if dashed == "no":
        return ""
    width = math.floor(float(width) * 3.8)
    dash = dashed.replace("dash", "10,5")
    dash = dash.replace("dot", "1,5")
    dash = dash.replace("solid", "")
    dash = dash.replace(" ", ",")
    dash = "[%s]" % dash
    if dash == "[]" or dash == "[no]":
        dash = "null"
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
    strokeString = ("stroke: new ol.style.Stroke({color: %s, lineDash: %s, " %
                    (color, dash))
    strokeString += ("lineCap: '%s', lineJoin: '%s', width: %d})," %
                     (capString, joinString, width))
    return strokeString


def getFillStyle(color, props):
    try:
        if props["style"] == "no":
            return ""
    except:
        QgsMessageLog.logMessage(traceback.format_exc(), "qgis2web",
                                 level=QgsMessageLog.CRITICAL)
    return "fill: new ol.style.Fill({color: %s})" % color
