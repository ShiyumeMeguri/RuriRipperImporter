"""Ruri Blender 材质栈运行时(生成物,勿手改)。

本平台产物同批同 stamp(e58771848619c111):每个生成栈一个 <栈名>.blend(模板节点组库,codegen 期
headless 物化),外加**全平台唯一的**本文件(平台 ruri_girlsfrontline 的包入口)—— 运行时 + 全部栈清单
(内联在 MANIFESTS,每条自带它的 blend 文件名与组名单)。

⛔ 插件数据零缓存,严禁写进 .blend。从产物取来的模板组、模板材质、参数表、中性图、合成树与它的图、顶点腿的树、
兜底灯 —— 本运行时造的与带进来的每一个数据块都经宿主的 plugin_data() 出生(运行时数据,存盘一个字节都不写)。
.blend 里只有内容:网格、贴图、关卡数据、材质记录(ruri_uber_*)、场景上记下的后处理输入、用户设置。
开文件与本模块注册时宿主跑 load pass:先清掉插件数据(旧文件里上一版存下的、上一次注册留下的),再按记录把
每张材质现编一遍(compile_all),其余派生态由各派生阶段现建。

清单内联而不是单出一个 json,是因为插件 register() 跑在 Blender 的**受限上下文**里
(bpy.data 此刻是 _RestrictData),那时读不了 .blend 里的任何东西,而宿主又要在注册期
拿 INTERFACE 建 PropertyGroup。这一个 .py 也是不可省的下限:.blend 没法把自己注册进宿主。

内核版本/stamp 对不上一律响亮拒绝,没有任何现场重建退路。
"""
import json
import math
import os
import time

import bpy

RUNTIME_KERNEL = 'rvg1'

if bpy.app.version < (5, 3, 0):
    raise RuntimeError('[Ruri] 产物按 Blender 5.3 基准物化(EEVEE 原生灯节点:Light Info / '
                       'Light Evaluation / Shadow Raycast / Light Accumulation);当前 %d.%d 没有这些节点。'
                       % bpy.app.version[:2])
MANIFESTS = json.loads(r'''[{"kernel":"rvg1","stamp":"e58771848619c111","names":{"panel_key":"ruri_character_uber_girlsfrontline","panel_title":"Ruri_GirlsFrontline_Uber \u53C2\u6570","template_mat":"Ruri GirlsFrontline Uber Tpl ","vtx_modifier":"Ruri GirlsFrontline Uber Vertex","vtx_tree_prefix":"Ruri GirlsFrontline Uber Vertex ","material_name":"Ruri_GirlsFrontline_Uber","st_slot":"_BaseMap","st_node":"RuriBaseMapST"},"identity":"486d3a787a1c1f5d","known_parts":["Standard","Face","Eyes","EyeBlendAdd","EyeBlendMultiply"],"material_keywords":[],"variant_uniform":"_CharaPartID","variant_values":{"Standard":0,"Face":1,"Eyes":2,"EyeBlendAdd":3,"EyeBlendMultiply":4},"default_part":"Standard","blend":"ruri_character_uber_girlsfrontline.blend","parts":{"Standard":{"crossings":[["X0_0",1,0,1],["X0_1",1,0,1],["X0_2",1,0,1],["X0_3",1,0,1],["X0_4",0,0,1],["X0_5",1,0,1],["X0_6",0,0,1],["X0_10",1,0,1],["X0_11",1,0,1],["X0_12",1,0,1],["X0_13",0,0,1],["X0_14",0,0,1],["X0_15",0,0,1],["X0_16",0,0,1],["X0_17",0,0,1],["X0_18",1,0,1],["X0_19",0,0,1],["X0_20",0,0,1],["X0_22",0,0,1],["X0_23",0,0,1],["X0_25",1,0,1],["X0_26",1,0,1],["X0_27",1,0,1],["X1_0",1,1,2],["X1_1",0,1,2],["X0_7",0,0,2],["X0_8",0,0,2],["X0_9",0,0,2],["X1_2",0,1,2],["X1_4",0,1,2],["X1_5",0,1,2],["X1_6",1,1,2],["X1_7",1,1,2],["X1_10",0,1,2],["X0_13",0,0,2],["X0_14",0,0,2],["X1_11",0,1,2],["X1_12",1,1,2],["X1_13",1,1,2],["X1_14",1,1,2],["X1_15",0,1,2],["X1_16",0,1,2],["X1_17",0,1,2],["X1_18",0,1,2],["X0_21",0,0,2],["X1_19",1,1,2],["X1_20",0,1,2],["X1_22",0,1,2],["X1_23",0,1,2],["X1_24",1,1,2],["X1_25",1,1,2],["X1_26",0,1,2],["X1_27",0,1,2],["X1_28",1,1,2],["X1_31",0,1,2],["X0_22",0,0,2],["X0_24",1,0,2],["X1_33",1,1,2],["X1_34",1,1,2],["X1_35",1,1,2],["X1_36",1,1,2],["X1_37",1,1,2],["X1_38",1,1,2],["X1_0",1,1,3],["X2_0",0,2,3],["X2_1",0,2,3],["X2_2",1,2,3],["X2_3",1,2,3],["X0_7",0,0,3],["X2_4",1,2,3],["X2_5",1,2,3],["X2_6",1,2,3],["X1_3",0,1,3],["X1_8",1,1,3],["X1_9",1,1,3],["X2_7",1,2,3],["X0_13",0,0,3],["X0_14",0,0,3],["X1_12",1,1,3],["X1_14",1,1,3],["X2_8",0,2,3],["X2_9",0,2,3],["X2_10",1,2,3],["X2_11",1,2,3],["X2_12",1,2,3],["X1_21",0,1,3],["X1_29",1,1,3],["X1_30",1,1,3],["X2_13",1,2,3],["X0_22",0,0,3],["X0_24",1,0,3],["X1_32",1,1,3],["X2_14",1,2,3],["X2_15",1,2,3],["X2_16",1,2,3],["X2_17",1,2,3]],"finals":{"ret_gBuffer0":[3,1],"ret_gBuffer0_w":[1,0],"ret_gBuffer1":[0,1],"ret_gBuffer1_w":[0,0],"ret_gBuffer2":[0,1],"ret_gBuffer2_w":[0,0],"ret_color":[3,1],"ret_color_w":[1,0],"ret_depth":[0,0],"ret_shadowMask":[0,1],"ret_shadowMask_w":[0,0],"ret_meshRenderingLayers":[0,0],"__clip":[1,0],"__color_unlit":[2,1],"__color_noloop":[3,1]},"fetches":[{"sock":"F0_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F1_BumpMap","slot":"_BumpMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.5,0.5,1],"neutral_alpha":1},{"sock":"F2_RMOSMap","slot":"_RMOSMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0,0,0],"neutral_alpha":0},{"sock":"F3_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F4_BlendTex","slot":"_BlendTex","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.2158605,0.2158605,0.2158605],"neutral_alpha":0.5},{"sock":"F5_RampMap","slot":"_RampMap","depth":2,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F6_BlendTex","slot":"_BlendTex","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.2158605,0.2158605,0.2158605],"neutral_alpha":0.5},{"sock":"F7_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F8_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F9_IBL_unity_SpecCube0","slot":"IBL_unity_SpecCube0","depth":1,"non_color":false,"extension":"REPEAT","point":false,"env":true,"mip":true,"derivative_mip":true,"neutral":[0.2158605,0.2158605,0.2158605],"neutral_alpha":0.5},{"sock":"F10_RMOTex","slot":"_RMOTex","depth":0,"non_color":true,"extension":"TEXTURE:_BaseMap","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F11_RampMap","slot":"_RampMap","depth":2,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F12_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F13_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F14_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F15_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F16_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F17_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1}],"capabilities":[{"sock":"C0_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C1_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C2_ShadowAttenuation","cap":"ShadowAttenuation","depth":1,"query":{"position":true},"results":{"":false},"absent":{"":[1]},"result":"[0,1] attenuation"},{"sock":"C3_AmbientIrradiance","cap":"AmbientIrradiance","depth":1,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C4_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C5_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C6_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C7_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C8_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C9_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C10_AdditionalLightCount","cap":"AdditionalLightCount","depth":0,"query":{},"results":{"":false},"absent":{"":[0]},"result":"light count"},{"sock":"C11_AdditionalLight","cap":"AdditionalLight","depth":0,"query":{"index":false,"position":true},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"punctual light record (direction toward light, linear radiance)"},{"sock":"C12_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C13_ShadowAttenuation","cap":"ShadowAttenuation","depth":1,"query":{"position":true},"results":{"":false},"absent":{"":[1]},"result":"[0,1] attenuation"},{"sock":"C14_AmbientIrradiance","cap":"AmbientIrradiance","depth":1,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C15_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C16_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C17_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C18_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C19_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C20_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C21_AdditionalLightCount","cap":"AdditionalLightCount","depth":0,"query":{},"results":{"":false},"absent":{"":[0]},"result":"light count"},{"sock":"C22_AdditionalLight","cap":"AdditionalLight","depth":0,"query":{"index":false,"position":true},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"punctual light record (direction toward light, linear radiance)"}],"zones":[],"params":[["_UseBumpMap","F",[0,0,0],0],["_BumpScale","F",[1,1,1],0],["_UseRMOSMap","F",[0,0,0],0],["_BaseColor","V4",[1,1,1],1],["_SurfaceType","F",[0,0,0],0],["_RoughnessIntensity","F",[1,1,1],0],["_MetallicIntensity","F",[1,1,1],0],["_SpecularIntensity","F",[1,1,1],0],["_OutlineShadowColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],1],["_OutlineColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],0.10000000149011612],["_OutlineIntensity","F",[1,1,1],0],["_BaseMap_ST","V4",[1,1,0],0],["_FinalTint","V4",[1,1,1],1],["_UseAlphaTest","F",[0,0,0],0],["_Cutoff","F",[0.15000000596046448,0.15000000596046448,0.15000000596046448],0],["_AnisotropicSpecular","F",[0,0,0],0],["_AdjustShadowBias","F",[0,0,0],0],["_ShadowBiasDistance","F",[0.10000000149011612,0.10000000149011612,0.10000000149011612],0],["_UseRampMap","F",[0,0,0],0],["_UseSpecularUV2","F",[0,0,0],0],["_AnisotropyShift","F",[0.05000000074505806,0.05000000074505806,0.05000000074505806],0],["_Anisotropy","F",[1,1,1],0],["_UseGIFlatten","F",[0,0,0],0],["_UseStockingFalloff","F",[0,0,0],0],["_AnisotropicGXX","F",[0,0,0],0],["_EmissiveIntensity","F",[1,1,1],0],["_StockingFalloffColor","V4",[0.10000000149011612,0,0],1],["_StockingCenterColor","V4",[1,1,1],1],["_StockingFalloffPower","F",[1,1,1],0],["__size_RampMap","V3",[1,1,1],0],["_OutlineWidth","F",[0.5,0.5,0.5],0],["GlobalCharOutlineZBias","F",[0,0,0],0],["_OutlineZBias","F",[0,0,0],0],["_UseDitherClip","F",[0,0,0],0],["_DitherAlpha","F",[1,1,1],0]],"switch_params":["_AdjustShadowBias","_AnisotropicGXX","_AnisotropicSpecular","_SurfaceType","_UseAlphaTest","_UseBumpMap","_UseDitherClip","_UseGIFlatten","_UseRMOSMap","_UseRampMap","_UseSpecularUV2","_UseStockingFalloff"],"segments":["Ruri GirlsFrontline Uber Standard s0","Ruri GirlsFrontline Uber Standard s1","Ruri GirlsFrontline Uber Standard s2","Ruri GirlsFrontline Uber Standard s3"],"outline":{"segments":["Ruri GirlsFrontline Uber Outline Standard s0"],"crossings":[],"finals":{"position":[0,1]},"fetches":[]}},"Face":{"crossings":[["X0_0",1,0,1],["X0_1",1,0,1],["X0_2",0,0,1],["X0_3",1,0,1],["X0_4",0,0,1],["X0_5",0,0,1],["X0_6",0,0,1],["X0_7",1,0,1],["X0_8",1,0,1],["X0_9",1,0,1],["X0_10",0,0,1],["X0_11",0,0,1],["X0_12",0,0,1],["X0_13",0,0,1],["X0_14",0,0,1],["X0_15",0,0,1],["X0_16",0,0,1],["X0_18",0,0,1],["X0_19",0,0,1],["X0_20",0,0,1],["X0_21",1,0,1],["X0_22",1,0,1],["X0_23",0,0,1],["X0_24",0,0,1],["X0_25",1,0,1],["X0_26",1,0,1],["X0_27",1,0,1],["X1_0",1,1,2],["X1_1",0,1,2],["X1_2",0,1,2],["X0_13",0,0,2],["X1_3",0,1,2],["X1_4",0,1,2],["X0_17",0,0,2],["X1_5",0,1,2],["X1_6",0,1,2],["X1_9",0,1,2],["X1_10",0,1,2],["X1_11",1,1,2],["X1_12",1,1,2],["X0_20",0,0,2],["X1_16",1,1,2],["X0_27",1,0,2],["X1_0",1,1,3],["X2_0",0,2,3],["X2_1",0,2,3],["X2_2",1,2,3],["X2_3",1,2,3],["X0_13",0,0,3],["X2_4",1,2,3],["X1_7",1,1,3],["X1_8",0,1,3],["X1_13",1,1,3],["X1_14",1,1,3],["X2_5",1,2,3],["X0_23",0,0,3],["X0_25",1,0,3],["X1_15",1,1,3],["X2_6",1,2,3],["X2_7",1,2,3]],"finals":{"ret_gBuffer0":[3,1],"ret_gBuffer0_w":[1,0],"ret_gBuffer1":[0,1],"ret_gBuffer1_w":[0,0],"ret_gBuffer2":[0,1],"ret_gBuffer2_w":[0,0],"ret_color":[3,1],"ret_color_w":[1,0],"ret_depth":[0,0],"ret_shadowMask":[0,1],"ret_shadowMask_w":[0,0],"ret_meshRenderingLayers":[0,0],"__clip":[1,0],"__color_unlit":[1,1],"__color_noloop":[3,1]},"fetches":[{"sock":"F0_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F1_BumpMap","slot":"_BumpMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.5,0.5,1],"neutral_alpha":1},{"sock":"F2_RMOSMap","slot":"_RMOSMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0,0,0],"neutral_alpha":0},{"sock":"F3_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F4_BlendTex","slot":"_BlendTex","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.2158605,0.2158605,0.2158605],"neutral_alpha":0.5},{"sock":"F5_BlendTex","slot":"_BlendTex","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.2158605,0.2158605,0.2158605],"neutral_alpha":0.5},{"sock":"F6_RampMap","slot":"_RampMap","depth":2,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F7_BlendTex","slot":"_BlendTex","depth":1,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.2158605,0.2158605,0.2158605],"neutral_alpha":0.5},{"sock":"F8_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F9_RampMap","slot":"_RampMap","depth":1,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F10_IBL_unity_SpecCube0","slot":"IBL_unity_SpecCube0","depth":0,"non_color":false,"extension":"REPEAT","point":false,"env":true,"mip":true,"derivative_mip":true,"neutral":[0.2158605,0.2158605,0.2158605],"neutral_alpha":0.5},{"sock":"F11_RampMap","slot":"_RampMap","depth":0,"non_color":true,"extension":"EXTEND","point":false,"env":false,"mip":false,"derivative_mip":false,"neutral":[1,1,1],"neutral_alpha":1}],"capabilities":[{"sock":"C0_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C1_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C2_ShadowAttenuation","cap":"ShadowAttenuation","depth":1,"query":{"position":true},"results":{"":false},"absent":{"":[1]},"result":"[0,1] attenuation"},{"sock":"C3_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C4_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C5_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C6_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C7_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C8_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C9_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C10_AdditionalLightCount","cap":"AdditionalLightCount","depth":0,"query":{},"results":{"":false},"absent":{"":[0]},"result":"light count"},{"sock":"C11_AdditionalLight","cap":"AdditionalLight","depth":0,"query":{"index":false,"position":true},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"punctual light record (direction toward light, linear radiance)"},{"sock":"C12_AdditionalLightKind","cap":"AdditionalLightKind","depth":0,"query":{"index":false},"results":{"":false},"absent":{"":[0]},"result":"0 directional / 1 punctual"}],"zones":[],"params":[["_UseBumpMap","F",[0,0,0],0],["_BumpScale","F",[1,1,1],0],["_UseRMOSMap","F",[0,0,0],0],["_BaseColor","V4",[1,1,1],1],["_SurfaceType","F",[0,0,0],0],["_RoughnessIntensity","F",[1,1,1],0],["_MetallicIntensity","F",[1,1,1],0],["_SpecularIntensity","F",[1,1,1],0],["_OutlineShadowColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],1],["_OutlineColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],0.10000000149011612],["_OutlineIntensity","F",[1,1,1],0],["_BaseMap_ST","V4",[1,1,0],0],["_FinalTint","V4",[1,1,1],1],["_UseAlphaTest","F",[0,0,0],0],["_Cutoff","F",[0.15000000596046448,0.15000000596046448,0.15000000596046448],0],["_UseSpecularUV2","F",[0,0,0],0],["_AdjustShadowBias","F",[0,0,0],0],["_ShadowBiasDistance","F",[0.10000000149011612,0.10000000149011612,0.10000000149011612],0],["_FaceLightDirAdjustment","F",[0,0,0],0],["_BlendSmoothness","F",[0.10000000149011612,0.10000000149011612,0.10000000149011612],0],["_UseRampMap","F",[0,0,0],0],["_AnisotropyShift","F",[0.05000000074505806,0.05000000074505806,0.05000000074505806],0],["_Anisotropy","F",[1,1,1],0],["_UseGIFlatten","F",[0,0,0],0],["__size_RampMap","V3",[1,1,1],0],["_OutlineWidth","F",[0.5,0.5,0.5],0],["GlobalCharOutlineZBias","F",[0,0,0],0],["_OutlineZBias","F",[0,0,0],0],["_UseDitherClip","F",[0,0,0],0],["_DitherAlpha","F",[1,1,1],0]],"switch_params":["_AdjustShadowBias","_FaceLightDirAdjustment","_SurfaceType","_UseAlphaTest","_UseBumpMap","_UseDitherClip","_UseGIFlatten","_UseRMOSMap","_UseRampMap","_UseSpecularUV2"],"segments":["Ruri GirlsFrontline Uber Face s0","Ruri GirlsFrontline Uber Face s1","Ruri GirlsFrontline Uber Face s2","Ruri GirlsFrontline Uber Face s3"],"outline":{"segments":["Ruri GirlsFrontline Uber Outline Standard s0"],"crossings":[],"finals":{"position":[0,1]},"fetches":[]}},"Eyes":{"crossings":[["X0_0",1,0,1],["X0_1",1,0,1],["X0_2",1,0,1],["X0_3",1,0,1],["X0_4",0,0,1],["X0_5",0,0,1],["X0_6",0,0,1],["X0_7",1,0,1],["X0_8",1,0,1],["X1_0",0,1,2],["X1_1",1,1,2],["X1_2",1,1,2],["X1_3",0,1,2],["X1_4",1,1,2],["X1_5",1,1,2],["X0_5",0,0,2],["X0_7",1,0,2],["X1_6",1,1,2],["X1_7",1,1,2],["X1_8",1,1,2]],"finals":{"ret_gBuffer0":[2,1],"ret_gBuffer0_w":[1,0],"ret_gBuffer1":[0,1],"ret_gBuffer1_w":[0,0],"ret_gBuffer2":[0,1],"ret_gBuffer2_w":[0,0],"ret_color":[2,1],"ret_color_w":[0,0],"ret_depth":[0,0],"ret_shadowMask":[0,1],"ret_shadowMask_w":[0,0],"ret_meshRenderingLayers":[0,0],"__clip":[1,0],"__color_unlit":[1,1],"__color_noloop":[2,1]},"fetches":[{"sock":"F0_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F1_BumpMap","slot":"_BumpMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.5,0.5,1],"neutral_alpha":1},{"sock":"F2_RMOSMap","slot":"_RMOSMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0,0,0],"neutral_alpha":0},{"sock":"F3_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F4_MainTex","slot":"_MainTex","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F5_Specularmap","slot":"_Specularmap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0,0,0],"neutral_alpha":0}],"capabilities":[{"sock":"C0_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C1_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C2_ShadowAttenuation","cap":"ShadowAttenuation","depth":1,"query":{"position":true},"results":{"":false},"absent":{"":[1]},"result":"[0,1] attenuation"},{"sock":"C3_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C4_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C5_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C6_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C7_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C8_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C9_AmbientIrradiance","cap":"AmbientIrradiance","depth":0,"query":{"normal":true},"results":{"":true},"absent":{"":[0,0,0]},"result":"linear irradiance"},{"sock":"C10_AdditionalLightCount","cap":"AdditionalLightCount","depth":0,"query":{},"results":{"":false},"absent":{"":[0]},"result":"light count"},{"sock":"C11_AdditionalLight","cap":"AdditionalLight","depth":0,"query":{"index":false,"position":true},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"punctual light record (direction toward light, linear radiance)"}],"zones":[],"params":[["_UseBumpMap","F",[0,0,0],0],["_BumpScale","F",[1,1,1],0],["_UseRMOSMap","F",[0,0,0],0],["_BaseColor","V4",[1,1,1],1],["_SurfaceType","F",[0,0,0],0],["_RoughnessIntensity","F",[1,1,1],0],["_MetallicIntensity","F",[1,1,1],0],["_SpecularIntensity","F",[1,1,1],0],["_OutlineShadowColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],1],["_OutlineColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],0.10000000149011612],["_OutlineIntensity","F",[1,1,1],0],["_BaseMap_ST","V4",[1,1,0],0],["_FinalTint","V4",[1,1,1],1],["_UseAlphaTest","F",[0,0,0],0],["_Cutoff","F",[0.15000000596046448,0.15000000596046448,0.15000000596046448],0],["unity_WorldTransformParams","V4",[0,0,0],0],["_CorneaParallax","F",[0.30000001192092896,0.30000001192092896,0.30000001192092896],0],["_SpecularParallax","F",[0.30000001192092896,0.30000001192092896,0.30000001192092896],0],["_ShadowBiasDistance","F",[0.10000000149011612,0.10000000149011612,0.10000000149011612],0],["_ShadowIntensity","F",[0.25,0.25,0.25],0]],"switch_params":["_SurfaceType","_UseAlphaTest","_UseBumpMap","_UseRMOSMap"],"segments":["Ruri GirlsFrontline Uber Eyes s0","Ruri GirlsFrontline Uber Eyes s1","Ruri GirlsFrontline Uber Eyes s2"]},"EyeBlendAdd":{"crossings":[["X0_0",1,0,1],["X0_1",1,0,1],["X0_2",1,0,1],["X0_3",0,0,1],["X0_4",0,0,1],["X0_5",0,0,1],["X0_6",1,0,1],["X0_7",1,0,1],["X1_0",0,1,2],["X0_4",0,0,2],["X0_6",1,0,2],["X1_1",1,1,2],["X1_2",1,1,2]],"finals":{"ret_gBuffer0":[2,1],"ret_gBuffer0_w":[1,0],"ret_gBuffer1":[0,1],"ret_gBuffer1_w":[0,0],"ret_gBuffer2":[0,1],"ret_gBuffer2_w":[0,0],"ret_color":[2,1],"ret_color_w":[1,0],"ret_depth":[0,0],"ret_shadowMask":[0,1],"ret_shadowMask_w":[0,0],"ret_meshRenderingLayers":[0,0],"__clip":[1,0],"__color_unlit":[1,1]},"fetches":[{"sock":"F0_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F1_BumpMap","slot":"_BumpMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.5,0.5,1],"neutral_alpha":1},{"sock":"F2_RMOSMap","slot":"_RMOSMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0,0,0],"neutral_alpha":0},{"sock":"F3_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F4_MainTex","slot":"_MainTex","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1}],"capabilities":[{"sock":"C0_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C1_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"},{"sock":"C2_ShadowAttenuation","cap":"ShadowAttenuation","depth":1,"query":{"position":true},"results":{"":false},"absent":{"":[1]},"result":"[0,1] attenuation"}],"zones":[],"params":[["_UseBumpMap","F",[0,0,0],0],["_BumpScale","F",[1,1,1],0],["_UseRMOSMap","F",[0,0,0],0],["_BaseColor","V4",[1,1,1],1],["_SurfaceType","F",[0,0,0],0],["_RoughnessIntensity","F",[1,1,1],0],["_MetallicIntensity","F",[1,1,1],0],["_SpecularIntensity","F",[1,1,1],0],["_OutlineShadowColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],1],["_OutlineColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],0.10000000149011612],["_OutlineIntensity","F",[1,1,1],0],["_BaseMap_ST","V4",[1,1,0],0],["_FinalTint","V4",[1,1,1],1],["_UseAlphaTest","F",[0,0,0],0],["_Cutoff","F",[0.15000000596046448,0.15000000596046448,0.15000000596046448],0],["_ShadowBiasDistance","F",[0.10000000149011612,0.10000000149011612,0.10000000149011612],0],["_MainColor","V4",[1,1,1],0.8500000238418579]],"switch_params":["_SurfaceType","_UseAlphaTest","_UseBumpMap","_UseRMOSMap"],"segments":["Ruri GirlsFrontline Uber EyeBlendAdd s0","Ruri GirlsFrontline Uber EyeBlendAdd s1","Ruri GirlsFrontline Uber EyeBlendAdd s2"]},"EyeBlendMultiply":{"crossings":[["X0_0",1,0,1],["X0_1",0,0,1],["X0_2",0,0,1],["X0_3",0,0,1],["X0_4",1,0,1],["X0_5",1,0,1]],"finals":{"ret_gBuffer0":[1,1],"ret_gBuffer0_w":[1,0],"ret_gBuffer1":[0,1],"ret_gBuffer1_w":[0,0],"ret_gBuffer2":[0,1],"ret_gBuffer2_w":[0,0],"ret_color":[1,1],"ret_color_w":[0,0],"ret_depth":[0,0],"ret_shadowMask":[0,1],"ret_shadowMask_w":[0,0],"ret_meshRenderingLayers":[0,0],"__clip":[1,0]},"fetches":[{"sock":"F0_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F1_BumpMap","slot":"_BumpMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0.5,0.5,1],"neutral_alpha":1},{"sock":"F2_RMOSMap","slot":"_RMOSMap","depth":0,"non_color":true,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[0,0,0],"neutral_alpha":0},{"sock":"F3_BaseMap","slot":"_BaseMap","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1},{"sock":"F4_MainTex","slot":"_MainTex","depth":0,"non_color":false,"extension":"TEXTURE","point":false,"env":false,"mip":false,"derivative_mip":true,"neutral":[1,1,1],"neutral_alpha":1}],"capabilities":[{"sock":"C0_MainLight","cap":"MainLight","depth":0,"query":{},"results":{"direction":true,"color":true,"distanceAttenuation":false,"shadowAttenuation":false,"layerMask":false},"absent":{"direction":[0,0,0],"color":[0,0,0],"distanceAttenuation":[0],"shadowAttenuation":[0],"layerMask":[0]},"result":"directional light record (direction toward light, linear radiance)"}],"zones":[],"params":[["_UseBumpMap","F",[0,0,0],0],["_BumpScale","F",[1,1,1],0],["_UseRMOSMap","F",[0,0,0],0],["_BaseColor","V4",[1,1,1],1],["_SurfaceType","F",[0,0,0],0],["_RoughnessIntensity","F",[1,1,1],0],["_MetallicIntensity","F",[1,1,1],0],["_SpecularIntensity","F",[1,1,1],0],["_OutlineShadowColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],1],["_OutlineColor","V4",[0.6000000238418579,0.6000000238418579,0.6000000238418579],0.10000000149011612],["_OutlineIntensity","F",[1,1,1],0],["_BaseMap_ST","V4",[1,1,0],0],["_FinalTint","V4",[1,1,1],1],["_UseAlphaTest","F",[0,0,0],0],["_Cutoff","F",[0.15000000596046448,0.15000000596046448,0.15000000596046448],0],["_MainColor","V4",[1,1,1],0.8500000238418579]],"switch_params":["_SurfaceType","_UseAlphaTest","_UseBumpMap","_UseRMOSMap"],"segments":["Ruri GirlsFrontline Uber EyeBlendMultiply s0","Ruri GirlsFrontline Uber EyeBlendMultiply s1"]}},"packing":{"_BaseMap":[["rgb","BaseColor",""],["a","Opacity",""]],"_BumpMap":[["rg","PackedTangentNormal","unpack_normal_alpha_green"]],"_RMOSMap":[["r","Roughness",""],["g","Metallic",""],["b","Occlusion",""],["a","SpecularLevel",""]]},"vertex_parts":{},"rig":{"bone":"Head_M","attr":"ruri_face_basis","prop":"ruri_rig_basis_bone","label":"\u57FA\u5EA7\u9AA8\u9ABC","parts":["Eyes","Face"]},"globals":{"_RuriOutlineShellGate":{"role":"outline_shell","type":"VALUE","default":[0,0,0],"default_w":0}},"object_attributes":{},"texel_sizes":{},"engine_global_sources":{},"level_images":{},"interface":[{"name":"\u57FA\u7840","gate":null,"rows":[{"name":"_RMOTex","label":"RMO Map (RGB)","kind":"TEXTURE"},{"name":"_BlendTex","label":"Blend Tex","kind":"TEXTURE"},{"name":"_MainTex","label":"MainTex","kind":"TEXTURE"},{"name":"_Specularmap","label":"Specular Map","kind":"TEXTURE"},{"name":"_BaseMap","label":"Albedo","kind":"TEXTURE","st_node":"RuriBaseMapST"},{"name":"_BumpMap","label":"Normal Map","kind":"TEXTURE"},{"name":"_RampMap","label":"Diffuse Ramp Map","kind":"TEXTURE"},{"name":"_EmissionMap","label":"Emission","kind":"TEXTURE"},{"name":"_OutlineMask","label":"Outline Mask","kind":"TEXTURE"},{"name":"_HairBrowMask","label":"Hair Brow Mask","kind":"TEXTURE"},{"name":"_RMOSMap","label":"RMOS Map (R=Rough G=Metal B=Occ A=Spec)","kind":"TEXTURE"},{"name":"_PositionTexture","label":"VAT \u4F4D\u7F6E\u56FE","kind":"TEXTURE"},{"name":"_RotationTexture","label":"VAT \u65CB\u8F6C\u56FE","kind":"TEXTURE"},{"name":"_CommonVATMap","label":"\u9AA8\u9ABC VAT \u52A8\u753B\u56FE","kind":"TEXTURE"},{"name":"_FactoryVATMap","label":"\u5DE5\u5382 VAT \u52A8\u753B\u56FE","kind":"TEXTURE"},{"name":"_ColorTexture","label":"VAT \u989C\u8272\u56FE","kind":"TEXTURE"},{"name":"_BlackBoxContourTexture","label":"\u9ED1\u7BB1\u8F6E\u5ED3\u56FE","kind":"TEXTURE"},{"name":"_ScanLineMaskTexture","label":"\u626B\u63CF\u7EBF\u906E\u7F69","kind":"TEXTURE"},{"name":"_SludgeHeightTexture","label":"\u6DE4\u6CE5\u9AD8\u5EA6\u56FE","kind":"TEXTURE"},{"name":"_DisappearTex","label":"\u6DE4\u6CE5\u6D88\u6563\u566A\u58F0","kind":"TEXTURE"},{"name":"_DiffuseMap","label":"Base Map","kind":"TEXTURE"},{"name":"_NormalMap","label":"Normal Map","kind":"TEXTURE"},{"name":"_ILMMap","label":"ILM Map","kind":"TEXTURE"},{"name":"_ColorRamp","label":"Color Ramp","kind":"TEXTURE"},{"name":"_MetalMatcap","label":"Metal Matcap","kind":"TEXTURE"},{"name":"_AddMatcapMap","label":"Add Matcap Map","kind":"TEXTURE"},{"name":"_DenierMap","label":"Denier Map","kind":"TEXTURE"},{"name":"_DyeingTex","label":"Dyeing Map","kind":"TEXTURE"},{"name":"_SpecularShiftTex","label":"Specular Shift Tex","kind":"TEXTURE"},{"name":"_Matcap","label":"Matcap","kind":"TEXTURE"},{"name":"_MakeupTex","label":"Makeup Tex","kind":"TEXTURE"},{"name":"_ExpressionMaskMap","label":"Expression Mask Map","kind":"TEXTURE"},{"name":"_HighLightTex1","label":"HightLightTex 1","kind":"TEXTURE"},{"name":"_HighLightTex2","label":"HightLightTex 2","kind":"TEXTURE"},{"name":"_HighLightTex3","label":"HightLightTex 3","kind":"TEXTURE"},{"name":"_AddMatcap","label":"Add Matcap","kind":"TEXTURE"},{"name":"Ramp","label":"\u5206\u533A\u659C\u5761\u56FE(\u4E94\u884C)","kind":"TEXTURE"},{"name":"MaskTex","label":"\u9762\u90E8\u9634\u5F71 SDF","kind":"TEXTURE"},{"name":"TypeMask","label":"\u5206\u533A\u906E\u7F69(A = \u5206\u533A\u53F7)","kind":"TEXTURE"},{"name":"RegionIDTex","label":"\u7F16\u53F7\u5206\u533A\u56FE","kind":"TEXTURE"},{"name":"Fur_Tex","label":"\u6BDB\u7ED2\u906E\u7F69","kind":"TEXTURE"},{"name":"HeightMap","label":"\u89C6\u5DEE\u6BDB\u7ED2\u9AD8\u5EA6\u573A","kind":"TEXTURE"},{"name":"FlowmapMap","label":"\u89C6\u5DEE\u6BDB\u7ED2\u6D41\u5411\u56FE","kind":"TEXTURE"},{"name":"HairUVMap","label":"\u53D1\u4E1D\u5750\u6807\u56FE","kind":"TEXTURE"},{"name":"HairNoise","label":"\u53D1\u4E1D\u566A\u58F0","kind":"TEXTURE"},{"name":"HairRamp","label":"\u9AD8\u5149\u5E26\u5256\u9762(\u56DB\u884C)","kind":"TEXTURE"},{"name":"_OffsetTex","label":"Offset Tex","kind":"TEXTURE"},{"name":"_OffsetMaskTex","label":"Offset Mask Tex","kind":"TEXTURE"},{"name":"_MainTex2","label":"Main Tex 2","kind":"TEXTURE"},{"name":"_InkSimulationResultA","label":"Ink Simulation A","kind":"TEXTURE"},{"name":"_InkSimulationResultB","label":"Ink Simulation B","kind":"TEXTURE"},{"name":"_FlowmapTex","label":"Flowmap Tex","kind":"TEXTURE"},{"name":"_AirWallTex","label":"AirWall Tex","kind":"TEXTURE"},{"name":"_EmissionTex","label":"Emission Tex","kind":"TEXTURE"},{"name":"_DisturbTex2","label":"Disturb Tex 2","kind":"TEXTURE"},{"name":"_WeightTex","label":"Weight Tex","kind":"TEXTURE"},{"name":"_EmissiveRampMap","label":"Emissive Ramp Map","kind":"TEXTURE"},{"name":"_SpreadTex","label":"Spread Tex","kind":"TEXTURE"},{"name":"_SampleTex0","label":"Sample Tex 0","kind":"TEXTURE"},{"name":"_SampleTex1","label":"Sample Tex 1","kind":"TEXTURE"},{"name":"_SampleTex2","label":"Sample Tex 2","kind":"TEXTURE"},{"name":"_SampleTex3","label":"Sample Tex 3","kind":"TEXTURE"},{"name":"_SampleTex4","label":"Sample Tex 4","kind":"TEXTURE"},{"name":"_SampleTex5","label":"Sample Tex 5","kind":"TEXTURE"},{"name":"_NREMap","label":"NRE Map (\u6CD5\u7EBF/\u81EA\u53D1\u5149\u906E\u7F69)","kind":"TEXTURE"},{"name":"_WireRampTex","label":"Wire Ramp Tex","kind":"TEXTURE"},{"name":"_BaseColorMap","label":"Wallhack Base Color Map","kind":"TEXTURE"},{"name":"_SequenceBaseColorMap","label":"\u5E8F\u5217\u5E27\u989C\u8272\u56FE","kind":"TEXTURE"},{"name":"_SequenceMotionVectorMap","label":"\u5E8F\u5217\u5E27Motion Vector\u56FE","kind":"TEXTURE"},{"name":"_SequenceNormalMap","label":"\u5E8F\u5217\u5E27\u6CD5\u7EBF\u56FE","kind":"TEXTURE"},{"name":"_DissolveTex","label":"Dissolve Tex","kind":"TEXTURE"},{"name":"_RefractTex","label":"Refract Tex","kind":"TEXTURE"},{"name":"_RainTex0","label":"Rain Tex 0","kind":"TEXTURE"},{"name":"_PositiveAxesLightmap","label":"Six-Way \u002BAxes Lightmap","kind":"TEXTURE"},{"name":"_NegativeAxesLightmap","label":"Six-Way -Axes Lightmap","kind":"TEXTURE"},{"name":"_BaseColor","label":"Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_BumpScale","label":"Normal Scale","kind":"VALUE","size":1,"default":[1]},{"name":"_ShadowSoft","label":"Shadow Soft","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_RoughnessIntensity","label":"Roughness Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_MetallicIntensity","label":"Metallic Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_SpecularIntensity","label":"Specular Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_EmissiveIntensity","label":"Emissive Intensity","kind":"SLIDER","size":1,"min":1,"max":20,"default":[1]},{"name":"_AlphaClip","label":"__clip","kind":"SWITCH","size":1,"default":[0]},{"name":"_AlphaClipThreshold","label":"\u0027Clip Threshold\u0027 {}","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_UseDitherClip","label":"Use Dither Clip","kind":"SWITCH","size":1,"default":[0]},{"name":"_DitherAlpha","label":"Dither Alpha Value","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_SurfaceType","label":"Surface Type","kind":"INT","size":2,"default":[0]},{"name":"_HairBrowMaskThreshold","label":"Hair Brow Mask Threshold","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_UseRMOSMap","label":"Use RMOS Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseRampMap","label":"Use Ramp Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseBumpMap","label":"Use Normal Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseEmission","label":"Use Emission","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseMaskUV2","label":"Use Mask UV2","kind":"SWITCH","size":1,"default":[0]},{"name":"_GameRenderStyle","label":"Render Style","kind":"VALUE","size":2,"default":[0]},{"name":"_CharaPartID","label":"Character Part","kind":"INT","size":2,"default":[0]},{"name":"_UseHairShadow","label":"Use Hair Shadow","kind":"INT","size":1,"default":[0]},{"name":"_EyeShadowIntensity","label":"Eye Shadow Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.20000000298023224]},{"name":"_UseAnisotropicSpecular","label":"Use Anisotropic Specular","kind":"SWITCH","size":1,"default":[0]},{"name":"_AnisotropicGGX","label":"Anisotropic GGX","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_Anisotropy","label":"Anisotropy","kind":"SLIDER","size":1,"min":0,"max":5,"default":[1]},{"name":"_AnisotropyShift","label":"Anisotropy Shift","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.05000000074505806]},{"name":"_UseStocking","label":"Use Stocking Falloff","kind":"SWITCH","size":1,"default":[0]},{"name":"_StockingCenterColor","label":"Stocking Center Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_StockingFalloffColor","label":"Stocking Falloff Color","kind":"COLOR","size":4,"default":[0.10000000149011612,0,0,1],"gamma":true},{"name":"_StockingFalloffPower","label":"Stocking Falloff Power","kind":"SLIDER","size":1,"min":0.1,"max":5,"default":[1]},{"name":"_EmissionColor","label":"Emission Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_OutlineWidth","label":"Outline Width","kind":"SLIDER","size":1,"min":0,"max":2,"default":[0.5]},{"name":"_OutlineOffsetZ","label":"Outline Offset Z","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_OutlineAverageNormal","label":"Use Smooth Normal (UV2)","kind":"SWITCH","size":1,"default":[1]},{"name":"_OutlineTintEnable","label":"Outline Tint Enable","kind":"SWITCH","size":1,"default":[0]},{"name":"_OutlineTintColor","label":"Outline Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_EnableOutlineMask","label":"Outline Mask Enable","kind":"SWITCH","size":1,"default":[1]},{"name":"_UseVertexColorOutline","label":"Use VertexColor Outline","kind":"SWITCH","size":1,"default":[0]},{"name":"_BackFaceNormalFlip","label":"Back Face Normal Flip","kind":"VALUE","size":1,"default":[0]},{"name":"_AlphaPremultiply","label":"Alpha Premultiply","kind":"VALUE","size":1,"default":[0]},{"name":"_FBXRotationFix","label":"FBX -90 Z Rotation Fix (OTW col0/col1 swap)","kind":"SWITCH","size":1,"default":[0]},{"name":"_NormalScale","label":"Normal Scale","kind":"VALUE","size":1,"default":[1]},{"name":"_FresnelColor","label":"Fresnel Color","kind":"COLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_EnableHoudiniVAT","label":"Houdini VAT","kind":"SWITCH","size":1,"default":[0]},{"name":"_HoudiniVATType","label":"VAT \u7C7B\u578B","kind":"VALUE","size":1,"default":[0]},{"name":"_HoudiniVATInParticle","label":"VAT \u5728\u7C92\u5B50\u91CC","kind":"SWITCH","size":1,"default":[0]},{"name":"_frameCount","label":"Frame Count","kind":"VALUE","size":1,"default":[0]},{"name":"_boundMaxX","label":"Bound Max X","kind":"VALUE","size":1,"default":[0]},{"name":"_boundMaxY","label":"Bound Max Y","kind":"VALUE","size":1,"default":[0]},{"name":"_boundMaxZ","label":"Bound Max Z","kind":"VALUE","size":1,"default":[0]},{"name":"_boundMinX","label":"Bound Min X","kind":"VALUE","size":1,"default":[0]},{"name":"_boundMinY","label":"Bound Min Y","kind":"VALUE","size":1,"default":[0]},{"name":"_boundMinZ","label":"Bound Min Z","kind":"VALUE","size":1,"default":[0]},{"name":"_B_autoPlayback","label":"Auto Playback","kind":"SWITCH","size":1,"default":[0]},{"name":"_gameTimeAtFirstFrame","label":"Game Time At First Frame","kind":"VALUE","size":1,"default":[0]},{"name":"_displayFrame","label":"Display Frame","kind":"VALUE","size":1,"default":[0]},{"name":"_playbackSpeed","label":"Playback Speed","kind":"VALUE","size":1,"default":[0]},{"name":"_houdiniFPS","label":"Houdini FPS","kind":"VALUE","size":1,"default":[0]},{"name":"_TextureFormat","label":"Texture Format (1=HDR 0=LDR)","kind":"SWITCH","size":1,"default":[1]},{"name":"_B_UNLOAD_ROT_TEX","label":"\u4F7F\u7528\u538B\u7F29ROT(\u5B58\u5728PosTex.a)","kind":"SWITCH","size":1,"default":[0]},{"name":"_B_surfaceNormals","label":"Support Surface Normal Maps","kind":"SWITCH","size":1,"default":[0]},{"name":"_B_pscaleAreInPosA","label":"Piece Scales Are In Position Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_globalPscaleMul","label":"Global Piece Scale Multiplier","kind":"VALUE","size":1,"default":[1]},{"name":"_EnableCommonVAT","label":"CommonVAT \u9876\u70B9\u52A8\u753B","kind":"SWITCH","size":1,"default":[0]},{"name":"_CommonVATMapParams","label":"VAT \u53C2\u6570(\u5BBD,\u9AD8,\u4FDD\u7559,\u4FDD\u7559)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_CommonVATCurrentFrame","label":"\u5F53\u524D\u5E27","kind":"VALUE","size":1,"default":[0]},{"name":"_CommonVATAutoPlay","label":"\u81EA\u52A8\u64AD\u653E","kind":"SWITCH","size":1,"default":[1]},{"name":"_CommonVATFPS","label":"\u52A8\u753B\u5E27\u7387","kind":"VALUE","size":1,"default":[30]},{"name":"_CommonVATBlendNormal","label":"\u6CD5\u7EBF\u6DF7\u5408\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_EnableFactoryVAT","label":"\u5DE5\u5382 VAT","kind":"SWITCH","size":1,"default":[0]},{"name":"_FactoryVATMapParams","label":"\u5DE5\u5382 VAT \u53C2\u6570(\u5BBD,\u9AD8,\u4FDD\u7559,\u4FDD\u7559)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_FactoryVATFrame","label":"\u5DE5\u5382 VAT \u5F53\u524D\u5E27","kind":"VALUE","size":1,"default":[0]},{"name":"_FactoryVATLastFrame","label":"\u5DE5\u5382 VAT \u4E0A\u4E00\u5E27","kind":"VALUE","size":1,"default":[0]},{"name":"_UseVATColorTex","label":"VAT \u989C\u8272\u56FE","kind":"SWITCH","size":1,"default":[0]},{"name":"_B_uvFromRG","label":"VAT \u989C\u8272\u56FE uv \u53D6 RG","kind":"SWITCH","size":1,"default":[0]},{"name":"_EffectPartID","label":"Effect Part ID","kind":"VALUE","size":1,"default":[0]},{"name":"_UseAlphaTest","label":"Use Alpha Test","kind":"SWITCH","size":1,"default":[0]},{"name":"_IgnorePostExposure","label":"Ignore Post Exposure","kind":"SWITCH","size":1,"default":[1]},{"name":"_CullMode","label":"Cull Mode","kind":"VALUE","size":1,"default":[2]},{"name":"_ExposureWithMiscParams","label":"Exposure (y = post exposure)","kind":"VECTOR","size":4,"default":[1,1,1,1]},{"name":"_VFXParams1","label":"VFX Grade (rgb = tint, w = saturation)","kind":"VECTOR","size":4,"default":[1,1,1,1]},{"name":"_VFXParams0","label":"VFX Params0 (xyz = \u89D2\u8272\u4F4D, w = \u65F6\u95F4)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_GlobalsFresnelColor","label":"Globals Fresnel Color","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"unity_Float4x5_Param0","label":"Per-Draw Instance Data 0","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_ScanLinePlane","label":"\u626B\u63CF\u5E73\u9762(xyz \u6CD5\u7EBF, w \u8DDD\u79BB)","kind":"VECTOR","size":4,"default":[0,1,0,0]},{"name":"_ScanLineWidth","label":"\u626B\u63CF\u7EBF\u5BBD","kind":"VALUE","size":1,"default":[0]},{"name":"_ScanLineColor","label":"\u626B\u63CF\u7EBF\u8272","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_BlackBoxColor","label":"\u9ED1\u7BB1\u8F6E\u5ED3\u8272","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_GridLineWidth","label":"Grid Line Width","kind":"SLIDER","size":1,"min":0.1,"max":15,"default":[1]},{"name":"_ForceMoveToFarPlane","label":"Move To Far Plane","kind":"SWITCH","size":1,"default":[0]},{"name":"_FocusScreenCenterInnerSize","label":"\u4E2D\u5FC3\u663E\u793A\u7684\u8303\u56F4","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_FocusScreenCenterOuterRange","label":"\u8FC7\u6E21\u5230\u4E0D\u663E\u793A\u7684\u8DDD\u79BB","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_EnableDepthOnlyDither","label":"\u6DF1\u5EA6\u8D9F\u6296\u52A8","kind":"SWITCH","size":1,"default":[0]},{"name":"_TaaFrameInfo","label":"TAA Frame Info","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_TransparentMatColor","label":"\u7A7F\u5899\u6750\u8D28\u8272","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_UseVFXPortal","label":"VFX Portal","kind":"SWITCH","size":1,"default":[0]},{"name":"_PortalStencilSet","label":"Portal Stencil Set (0=\u906E\u7F69 1=\u5BB9\u5668 2=\u53CD\u5411)","kind":"VALUE","size":1,"default":[0]},{"name":"_PortalAlpha","label":"Portal Alpha","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_UseSludge","label":"Use Sludge","kind":"SWITCH","size":1,"default":[0]},{"name":"_SludgeHeightTextureParams0","label":"Sludge Params0 (xyz \u539F\u70B9, w \u8303\u56F4)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SludgeHeightTextureParams1","label":"Sludge Params1 (v \u8F74)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SludgeHeightTextureParams2","label":"Sludge Params2 (u \u8F74)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SludgeHeightTextureParams3","label":"Sludge Params3 (uv \u7F29\u653E)","kind":"VECTOR","size":4,"default":[1,1,0,0]},{"name":"_SludgeHeightTextureEdgeSharp","label":"Sludge Edge Sharp","kind":"VALUE","size":1,"default":[20]},{"name":"_DisappearTexNoiseIntensity","label":"Disappear Noise Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_DisappearDarkSpeed","label":"Disappear Dark Speed","kind":"VALUE","size":1,"default":[0]},{"name":"_DisappearEdgeSharp","label":"Disappear Edge Sharp","kind":"VALUE","size":1,"default":[1]},{"name":"_DisappearCenterPosition","label":"Disappear Center","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_DisappearColor","label":"Disappear Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_DitherScale","label":"Dither Scale","kind":"VALUE","size":1,"default":[1]},{"name":"_CharacterInteractiveRange","label":"Character Interactive Range","kind":"VALUE","size":1,"default":[1]},{"name":"_CharacterInteractiveColor","label":"Character Interactive Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_CharacterInteractiveParam","label":"Character Interactive Param (xyz \u53D7\u51FB\u70B9, w \u76F8\u4F4D)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_UseNoise3D","label":"Use Noise 3D","kind":"SWITCH","size":1,"default":[0]},{"name":"_NoiseIntensity","label":"\u6270\u52A8\u56FE\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_NoiseTexUseWorldUVW","label":"\u4F7F\u7528\u4E16\u754C\u5750\u6807\u505AUVW","kind":"SWITCH","size":1,"default":[1]},{"name":"_NoiseTexTilling1","label":"\u6270\u52A8\u56FEUV\u5BC6\u5EA6\u7F29\u653E1","kind":"VECTOR","size":4,"default":[1,1,1,0]},{"name":"_NoiseUVWDir1","label":"\u6270\u52A8\u56FEUVW\u79FB\u52A8\u65B9\u54111","kind":"VECTOR","size":4,"default":[1,1,1,0]},{"name":"_NoiseUVWSpeed1","label":"\u6270\u52A8\u56FEUVW\u79FB\u52A8\u901F\u5EA61","kind":"VALUE","size":1,"default":[0]},{"name":"_NoiseFar","label":"\u8FDC\u8FD1\u5206\u5272\u7EBF","kind":"VALUE","size":1,"default":[1]},{"name":"_NoiseFarIntensity","label":"\u8FDC\u5904\u6270\u52A8\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"_UseTrail","label":"Use Trail","kind":"SWITCH","size":1,"default":[0]},{"name":"_TrailEffect","label":"Trail Effect","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseBlend","label":"Use Blend","kind":"SWITCH","size":1,"default":[0]},{"name":"_BlendTexUVWeights","label":"Blend Tex UV Weights","kind":"VECTOR","size":4,"default":[1,0,0,0]},{"name":"_BlendTexUVSpeed","label":"Blend Tex UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_BlendTexUVRotateMat","label":"Blend Tex UV Rotate Mat","kind":"VECTOR","size":4,"default":[1,0,0,1]},{"name":"_BlendTexUseDisturb","label":"Blend Tex Use Disturb","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_UseWaterBlend","label":"Use Water Blend","kind":"SWITCH","size":1,"default":[0]},{"name":"_WaterHeight","label":"Water Height","kind":"VALUE","size":1,"default":[-9999]},{"name":"_SafeFullAbsorpDistance","label":"Safe Full Absorp Distance","kind":"VALUE","size":1,"default":[1]},{"name":"_WaterAbsorption","label":"Water Absorption","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_WaterAbsorption2","label":"Water Absorption2","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_WaterScatter","label":"Water Scatter","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_UseFog","label":"Use Fog","kind":"SWITCH","size":1,"default":[0]},{"name":"_FogIntensity","label":"Fog Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_ShadowReceiverPartID","label":"Shadow Receiver Part ID","kind":"VALUE","size":1,"default":[0]},{"name":"_WaterPartID","label":"Water Part ID","kind":"VALUE","size":1,"default":[0]},{"name":"_ALerpIntensity","label":"Color(A) Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_AMax","label":"Max","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_AMin","label":"Min","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_AOClamp_Rump","label":"Rump AO \u6700\u5C0F\u503C","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_AOColorSaturation","label":"AO\u989C\u8272\u9971\u548C\u5EA6","kind":"SLIDER","size":1,"min":0,"max":3,"default":[1]},{"name":"_AOIntensity","label":"AO\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_AOIntensity_Rump","label":"Rump AO\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_AORampPower","label":"Ramp AO\u8303\u56F4","kind":"SLIDER","size":1,"min":0.1,"max":20,"default":[1]},{"name":"_AddMatCapOn","label":"\u542F\u7528\u9644\u52A0Matcap","kind":"SWITCH","size":1,"default":[0]},{"name":"_AddMatcapBrightness","label":"\u9644\u52A0Matcap\u4EAE\u5EA6","kind":"SLIDER","size":1,"min":0,"max":50,"default":[1]},{"name":"_AddMatcapColor","label":"\u9644\u52A0Matcap\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_AddMatcapHue","label":"\u9644\u52A0Matcap\u8272\u76F8","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_AddMatcapSaturation","label":"\u9644\u52A0Matcap\u9971\u548C\u5EA6","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_AnisotropyBias","label":"Anisotropy Bias","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0]},{"name":"_AnisotropyHueColor","label":"Anisotropy Hue","kind":"COLOR","size":4,"default":[1,1,1,0],"gamma":true},{"name":"_AnisotropySmoothness","label":"Anisotropy Smoothness","kind":"SLIDER","size":1,"min":0.01,"max":0.5,"default":[0.20000000298023224]},{"name":"_BLerpIntensity","label":"Color(B) Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_BackFresnelMax","label":"\u8FB9\u7F18\u5149\uFF08\u80CC\u5149\uFF09\u6700\u5927\u503C","kind":"SLIDER","size":1,"min":-1,"max":2,"default":[1]},{"name":"_BackFresnelMin","label":"\u8FB9\u7F18\u5149\uFF08\u80CC\u5149\uFF09\u6700\u5C0F\u503C","kind":"SLIDER","size":1,"min":-1,"max":2,"default":[0.5]},{"name":"_BackRimIntensity","label":"\u8FB9\u7F18\u5149\uFF08\u80CC\u5149\uFF09\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[0]},{"name":"_BrightenColor","label":"\u5C40\u90E8\u63D0\u4EAE\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorA","label":"Color(A)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorB","label":"Color(B)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorG","label":"Color(G)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorMax","label":"Color Max","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorMaxR","label":"\u5934\u53D1\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorMin","label":"Color Min","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorMinR","label":"\u5934\u53D1\u8FC7\u6E21\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorSaturation","label":"\u6697\u90E8\u9971\u548C\u5EA6\u8C03\u6574","kind":"VALUE","size":1,"default":[1]},{"name":"_DarkColor","label":"Dark Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DayColor","label":"Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DebugDiffseLayer","label":"Debug \u6F2B\u53CD\u5C04\u5C42","kind":"VALUE","size":1,"default":[0]},{"name":"_DebugSpecularLayer","label":"Debug \u9AD8\u5149\u5C42","kind":"VALUE","size":1,"default":[0]},{"name":"_DiffuseColorInfluence","label":"\u56FA\u6709\u8272\u5F71\u54CD\u81EA\u53D1\u5149\u989C\u8272","kind":"SLIDER","size":1,"min":0,"max":5,"default":[0]},{"name":"_DyeingColor","label":"\u7EA2\u901A\u9053\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DyeingColor2","label":"\u7EFF\u901A\u9053\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DyeingColor3","label":"\u84DD\u901A\u9053\u989C\u8272\uFF08\u76AE\u80A4\uFF09","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DyeingColor4","label":"Alpha\u901A\u9053\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DyeingColorA","label":"\u776B\u6BDB\u53CD\u5149 (A)","kind":"COLOR","size":4,"default":[1,1,1,0],"gamma":true},{"name":"_DyeingColorAMax","label":"\u4E0A\u776B\u6BDB Max","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DyeingColorAMin","label":"\u4E0A\u776B\u6BDB Min","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DyeingColorB","label":"\u5507\u5F69 (B)","kind":"COLOR","size":4,"default":[1,1,1,0],"gamma":true},{"name":"_DyeingColorG","label":"\u816E\u7EA2 (G)","kind":"COLOR","size":4,"default":[1,1,1,0],"gamma":true},{"name":"_DyeingColorR","label":"\u773C\u5F71 (R)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DyeingIntensity4","label":"Alpha\u901A\u9053\u989C\u8272\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_DyeingLerpMode","label":"\u5207\u6362\u4E3ALerp\u6A21\u5F0F","kind":"SWITCH","size":1,"default":[0]},{"name":"_EmissiveGetRampEffect","label":"\u81EA\u53D1\u5149\u533A\u57DF\u53D7Ramp\u5F71\u54CD","kind":"SWITCH","size":1,"default":[0]},{"name":"_EmissiveRampMode","label":"  \u81EA\u53D1\u5149\u533A\u57DFRamp\u8DDF\u968F\u533A\u57DF","kind":"VALUE","size":2,"default":[3]},{"name":"_ExpressionColorMask","label":"Expresssion Color Mask","kind":"VALUE","size":1,"default":[0]},{"name":"_ExpressionIntensity","label":"Expression Intensity","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_ExpressionMaskColor","label":"Expression Mask Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ExpressionMaskIntensity","label":"Expression Mask Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_ExpressionOffsetU","label":"Expression OffsetU","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_ExpressionOffsetV","label":"Expression OffsetV","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_ExpressionScale3","label":"Expression Scale 3","kind":"SLIDER","size":1,"min":0.2,"max":2,"default":[1]},{"name":"_EyebrowDarkColor","label":"\u7709\u6BDB\u6697\u90E8\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_EyebrowNoRamp","label":"\u7709\u6BDB\u4E0D\u4F7F\u7528Ramp","kind":"SWITCH","size":1,"default":[0]},{"name":"_FadeClip","label":"Fade Threshold","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_FresnelMax","label":"\u8FB9\u7F18\u5149\u6700\u5927\u503C","kind":"SLIDER","size":1,"min":-1,"max":2,"default":[1]},{"name":"_FresnelMin","label":"\u8FB9\u7F18\u5149\u6700\u5C0F\u503C","kind":"SLIDER","size":1,"min":-1,"max":2,"default":[0.5]},{"name":"_FresnelPow","label":"_FresnelPow","kind":"VALUE","size":1,"default":[5]},{"name":"_GLerpIntensity","label":"Color(G) Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_HighLightColor1","label":"Hight Light Color 1","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_HighLightColor2","label":"Hight Light Color 2","kind":"COLOR","size":4,"default":[0.4392000138759613,0.8352000117301941,1,1],"gamma":true},{"name":"_HighLightIntensity2","label":"\u5634\u5507\u9AD8\u5149(\u5E38\u9A7B) \u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_HighLightMoveDistance2","label":"High Light Move Distance","kind":"SLIDER","size":1,"min":0,"max":0.02,"default":[0.019999999552965164]},{"name":"_HighlightIntensity1","label":"Intensity 1","kind":"SLIDER","size":1,"min":0,"max":2,"default":[2]},{"name":"_HighlightIntensity2","label":"Intensity 2","kind":"SLIDER","size":1,"min":0,"max":2,"default":[0.05999999865889549]},{"name":"_HighlightIntensity3","label":"Intensity 3","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_HorizontalAmount2","label":"Horizontal Amount","kind":"VALUE","size":1,"default":[4]},{"name":"_IsLumInverse","label":"\u53CD\u5411ToneMap","kind":"SWITCH","size":1,"default":[1]},{"name":"_KiboEnable","label":"\u5947\u6CE2\u5149\u7167\u8BBE\u7F6E","kind":"SWITCH","size":1,"default":[0]},{"name":"_Layer3Mode","label":"Layer3 Mode","kind":"VALUE","size":1,"default":[0]},{"name":"_MakeupColor","label":"Makeup Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_MaskColor02","label":"\u4E0B\u776B\u6BDB (0.2)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_MaskColor03","label":"\u773C\u767D\u7259\u9F7F (0.4)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_MaskColor04","label":"\u820C\u5934 (0.3)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_MaskColor05","label":"\u53E3\u8154\u7259\u5E8A (0.5)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_MaskColor06","label":"\u776B\u6BDB\u63CF\u8FB9 (0.6)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_MaskColor07","label":"NPC\u773C\u775B (0.7)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_MaskColor08","label":"NPC\u7709\u6BDB (0.8)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_Max","label":"Max","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_MetalMapOff","label":"\u5173\u95EDILM\u91D1\u5C5E\u5EA6(\u4EC5\u9644\u52A0Matcap\u90E8\u5206)","kind":"SWITCH","size":1,"default":[0]},{"name":"_Min","label":"Min","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_NormalMapOn","label":"Normal Map On","kind":"SWITCH","size":1,"default":[0]},{"name":"_PaintHighlightDayColor","label":"\u9AD8\u5149\u989C\u8272(G\u901A\u9053)","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_PartColor","label":"\u6311\u67D3\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_RampColor","label":"Ramp\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_RampPartColor","label":"Ramp\u6311\u67D3\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_Range","label":"Range","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_RimColor","label":"\u8FB9\u7F18\u5149\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_RimIntensity","label":"\u8FB9\u7F18\u5149\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[0]},{"name":"_RimLightColor","label":"_RimLightColor","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_RimLightColorRatio","label":"\u56FA\u6709\u8272\u989C\u8272\u5F71\u54CD\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_RimLightStrength","label":"_RimLightStrength","kind":"VALUE","size":1,"default":[0]},{"name":"_RimMainLightRatio","label":"\u4E3B\u5149\u6E90\u989C\u8272\u5F71\u54CD\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.20000000298023224]},{"name":"_RimMaskVal","label":"\u9876\u70B9\u8272\u63A7\u5236\u6BD4\u4F8B","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_RimNoiseIntensity","label":"Noise\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_RimNoiseMax","label":"Noise\u6700\u5927\u503C","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_RimNoiseMin","label":"Noise\u6700\u5C0F\u503C","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_RimNoiseScale","label":"Noise\u5927\u5C0F","kind":"VALUE","size":1,"default":[10]},{"name":"_RotateAngle1","label":"Anim Rotate Angle 1","kind":"SLIDER","size":1,"min":-180,"max":180,"default":[0]},{"name":"_RotateAngle2","label":"Anim Rotate Angle 2","kind":"SLIDER","size":1,"min":-180,"max":180,"default":[0]},{"name":"_RotateAngle3","label":"Anim Rotate Angle 3","kind":"SLIDER","size":1,"min":-180,"max":180,"default":[0]},{"name":"_ScaleX1","label":"Scale X 1","kind":"SLIDER","size":1,"min":0.2,"max":2,"default":[1]},{"name":"_ScaleX2","label":"Scale X 2","kind":"SLIDER","size":1,"min":0.2,"max":2,"default":[1]},{"name":"_ScaleY1","label":"Scale Y 1","kind":"SLIDER","size":1,"min":0.2,"max":2,"default":[1]},{"name":"_ScaleY2","label":"Scale Y 2","kind":"SLIDER","size":1,"min":0.2,"max":2,"default":[1]},{"name":"_ShiftIntensity","label":"\u6270\u52A8\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_SiwaBlackWhiteExchange","label":"\u9ED1\u767D\u4E1D\u889C\u8D28\u611F\u8F6C\u6362","kind":"SWITCH","size":1,"default":[0]},{"name":"_SiwaColor","label":"\u4E1D\u889C\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_SiwaFresnelMax","label":"\u4E1D\u889C\u83F2\u6D85\u5C14\u6700\u5927\u503C","kind":"VALUE","size":1,"default":[0]},{"name":"_SiwaFresnelMin","label":"\u4E1D\u889C\u83F2\u6D85\u5C14\u6700\u5C0F\u503C","kind":"VALUE","size":1,"default":[0]},{"name":"_SkinColor","label":"\u76AE\u80A4\u8C03\u8272","kind":"COLOR","size":4,"default":[1,1,1,0],"gamma":true},{"name":"_SpecularColor","label":"\u9AD8\u5149\u989C\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_SpecularExponent","label":"\u9AD8\u5149\u5BBD\u7A84","kind":"SLIDER","size":1,"min":0.01,"max":500,"default":[50]},{"name":"_SpecularFlipHorizontal","label":"Flip Horizontal","kind":"SWITCH","size":2,"default":[0]},{"name":"_SwitchToMultiply","label":"\u5207\u6362\u6B63\u7247\u53E0\u5E95\u53E0\u52A0\u6A21\u5F0F","kind":"SWITCH","size":2,"default":[0]},{"name":"_TestAngle","label":"Test Light Angle","kind":"SLIDER","size":1,"min":0,"max":360,"default":[0]},{"name":"_UseBrighten","label":"Use Brighten","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseDiffuse","label":"\u776B\u6BDB\u989C\u8272\u4F7F\u7528Base Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseRampPartColor","label":"\u6311\u67D3\u5904\u4F7F\u7528\u7279\u6709\u989C\u8272","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseRimNoiseMask","label":"Noise\u906E\u7F69\u5F00\u5173","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseTestLightDir","label":"Use Test Light Direction ?","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseVertexColorForColor","label":"Use Vertex Color(G) For Color","kind":"SWITCH","size":1,"default":[0]},{"name":"_VerticalAmount2","label":"Vertical Amount","kind":"VALUE","size":1,"default":[2]},{"name":"RampIdRegion0","label":"\u5206\u533A 0 \u659C\u5761\u884C","kind":"VALUE","size":1,"default":[0]},{"name":"RampIdRegion1","label":"\u5206\u533A 1 \u659C\u5761\u884C","kind":"VALUE","size":1,"default":[1]},{"name":"RampIdRegion2","label":"\u5206\u533A 2 \u659C\u5761\u884C","kind":"VALUE","size":1,"default":[2]},{"name":"RampIdRegion3","label":"\u5206\u533A 3 \u659C\u5761\u884C","kind":"VALUE","size":1,"default":[3]},{"name":"RampIdRegion4","label":"\u5206\u533A 4 \u659C\u5761\u884C","kind":"VALUE","size":1,"default":[4]},{"name":"RampIntensityRegion0","label":"\u5206\u533A 0 \u659C\u5761\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"RampIntensityRegion1","label":"\u5206\u533A 1 \u659C\u5761\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"RampIntensityRegion2","label":"\u5206\u533A 2 \u659C\u5761\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"RampIntensityRegion3","label":"\u5206\u533A 3 \u659C\u5761\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"RampIntensityRegion4","label":"\u5206\u533A 4 \u659C\u5761\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"UseRampIDMask","label":"\u6309\u5206\u533A\u9009\u659C\u5761\u884C","kind":"SWITCH","size":1,"default":[0]},{"name":"RampPosition","label":"\u659C\u5761\u884C\u4F4D\u7F6E","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.30000001192092896]},{"name":"RampInt","label":"\u659C\u5761\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"SkinMin","label":"\u76AE\u80A4\u5206\u533A\u4E0B\u754C","kind":"VALUE","size":1,"default":[0]},{"name":"SkinMax","label":"\u76AE\u80A4\u5206\u533A\u4E0A\u754C","kind":"VALUE","size":1,"default":[0]},{"name":"UseFaceAni_SkinMask","label":"\u975E\u76AE\u80A4\u5206\u533A\u659C\u5761\u6743\u91CD","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"SkinColor","label":"\u76AE\u80A4\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"GlobalCharSkyLightIntensity","label":"\u5929\u5149\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"DecodeShadowThreshold","label":"\u70D8\u7119\u9634\u5F71\u89E3\u7801\u9608\u503C","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.5]},{"name":"SolidShadowProcess","label":"\u5B9E\u5FC3\u9634\u5F71\u9608\u503C","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.20000000298023224]},{"name":"SolidShadowWidth","label":"\u5B9E\u5FC3\u9634\u5F71\u534A\u5BBD","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.10000000149011612]},{"name":"ShadowWidth","label":"\u9634\u5F71\u8F6F\u8FB9\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.009999999776482582]},{"name":"ShadowWidthUseID","label":"\u6309\u5206\u533A\u9009\u8F6F\u8FB9\u5BBD\u5EA6","kind":"SWITCH","size":1,"default":[0]},{"name":"ShadowWidthRegion0","label":"\u5206\u533A 0 \u8F6F\u8FB9\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.009999999776482582]},{"name":"ShadowWidthRegion1","label":"\u5206\u533A 1 \u8F6F\u8FB9\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.009999999776482582]},{"name":"ShadowWidthRegion2","label":"\u5206\u533A 2 \u8F6F\u8FB9\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.009999999776482582]},{"name":"ShadowWidthRegion3","label":"\u5206\u533A 3 \u8F6F\u8FB9\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.009999999776482582]},{"name":"ShadowWidthRegion4","label":"\u5206\u533A 4 \u8F6F\u8FB9\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0.001,"max":1,"default":[0.009999999776482582]},{"name":"OpacityIntensity","label":"\u4E0D\u900F\u660E\u5EA6\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"TranslucentDitherVaule","label":"\u6296\u52A8\u88C1\u5207\u4FDD\u5E95","kind":"VALUE","size":1,"default":[1.2000000476837158]},{"name":"NormalStrength","label":"\u6CD5\u7EBF\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"SolidShadowStrength","label":"\u5B9E\u5FC3\u9634\u5F71\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"Desaturation","label":"\u53BB\u9971\u548C","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"ShadowProcess","label":"\u9762\u90E8\u9634\u5F71\u4E2D\u5FC3\u4F4D","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"SolidShadow_RampPosition","label":"\u9762\u90E8\u9634\u5F71\u4E0A\u9650","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"SDFPitchPosition","label":"\u9762\u90E8\u56FE\u4FEF\u4EF0\u5206\u5F20","kind":"VALUE","size":1,"default":[0]},{"name":"KuroCharacterAmbientColor","label":"\u89D2\u8272\u73AF\u5883\u8272","kind":"COLOR","size":4,"default":[0.30054399371147156,0.40724000334739685,0.5028859972953796,1],"gamma":true},{"name":"KuroCharacterMainLightColor","label":"\u89D2\u8272\u4E3B\u5149\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"NearCharLightColor","label":"\u8FD1\u666F\u89D2\u8272\u4E3B\u5149\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"NearCharShadowColor","label":"\u8FD1\u666F\u89D2\u8272\u73AF\u5883\u8272","kind":"COLOR","size":4,"default":[0.30054399371147156,0.40724000334739685,0.5028859972953796,1],"gamma":true},{"name":"NearCharDistance","label":"\u8FD1\u666F\u5206\u754C\u8DDD\u79BB","kind":"VALUE","size":1,"default":[0]},{"name":"NearCharDistanceSoft","label":"\u8FD1\u666F\u5206\u754C\u67D4\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"KuroCharacterGlobalBossShadow","label":"\u89D2\u8272\u9634\u5F71\u8FC7\u6E21\u7A97","kind":"VECTOR","size":4,"default":[0.30000001192092896,0.699999988079071,0.20000000298023224,0]},{"name":"KuroCharacterGlobalShadowIntensity","label":"\u89D2\u8272\u5168\u5C40\u9634\u5F71\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"KuroCharacterSSSIntensity","label":"\u89D2\u8272\u6B21\u8868\u9762\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"KuroUseNewCharacterRim","label":"\u65B0\u7248\u89D2\u8272\u8FB9\u7F18\u5149","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"KuroCharacterRimColor","label":"\u89D2\u8272\u8FB9\u7F18\u5149\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"KuroToonRimColorScale","label":"\u8FB9\u7F18\u5149\u8D9F\u989C\u8272\u7F29\u653E","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"KuroToonOcclusionScale","label":"\u9634\u5F71\u91CF\u7F29\u653E","kind":"VALUE","size":1,"default":[1]},{"name":"ToonRimWidth","label":"\u8FB9\u7F18\u5149\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"SSAOIntensity","label":"\u5C4F\u5E55\u906E\u853D\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"SSAOSkinIntensity","label":"\u76AE\u80A4\u5C4F\u5E55\u906E\u853D\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"SubsurfaceColor","label":"\u6B21\u8868\u9762\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"SkinSubsurfaceColor","label":"\u76AE\u80A4\u6B21\u8868\u9762\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"AniSwitch_ShadowColorBright","label":"\u6697\u9762\u8272\u4EAE\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightPosition0","label":"\u9AD8\u5149\u5E26\u4F4D\u7F6E 0","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"HighlightPosition1","label":"\u9AD8\u5149\u5E26\u4F4D\u7F6E 1","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"HighlightPosition2","label":"\u9AD8\u5149\u5E26\u4F4D\u7F6E 2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"HighlightWidth0","label":"\u9AD8\u5149\u5E26\u534A\u5BBD 0","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightWidth1","label":"\u9AD8\u5149\u5E26\u534A\u5BBD 1","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightWidth2","label":"\u9AD8\u5149\u5E26\u534A\u5BBD 2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightScale0","label":"\u9AD8\u5149\u5E26\u7F29\u653E 0","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightScale1","label":"\u9AD8\u5149\u5E26\u7F29\u653E 1","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightScale2","label":"\u9AD8\u5149\u5E26\u7F29\u653E 2","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightFlowMin0","label":"\u9AD8\u5149\u5E26\u7F29\u653E\u4E0B\u9650 0","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightFlowMin1","label":"\u9AD8\u5149\u5E26\u7F29\u653E\u4E0B\u9650 1","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightFlowMin2","label":"\u9AD8\u5149\u5E26\u7F29\u653E\u4E0B\u9650 2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightEdgeLift0","label":"\u9AD8\u5149\u5E26\u8F6E\u5ED3\u62AC\u5347 0","kind":"VALUE","size":1,"default":[0]},{"name":"HighlightEdgeLift1","label":"\u9AD8\u5149\u5E26\u8F6E\u5ED3\u62AC\u5347 1","kind":"VALUE","size":1,"default":[0]},{"name":"HighlightEdgeLift2","label":"\u9AD8\u5149\u5E26\u8F6E\u5ED3\u62AC\u5347 2","kind":"VALUE","size":1,"default":[0]},{"name":"HairMaskChannel0","label":"\u9AD8\u5149\u5E26\u906E\u7F69\u901A\u9053 0","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HairMaskChannel1","label":"\u9AD8\u5149\u5E26\u906E\u7F69\u901A\u9053 1","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HairMaskChannel2","label":"\u9AD8\u5149\u5E26\u906E\u7F69\u901A\u9053 2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HairRimHiddenChannel0","label":"\u9AD8\u5149\u5E26\u8FB9\u7F18\u9690\u85CF\u901A\u9053 0","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HairRimHiddenChannel1","label":"\u9AD8\u5149\u5E26\u8FB9\u7F18\u9690\u85CF\u901A\u9053 1","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HairRimHiddenChannel2","label":"\u9AD8\u5149\u5E26\u8FB9\u7F18\u9690\u85CF\u901A\u9053 2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightColorIntensity0","label":"\u9AD8\u5149\u5E26\u4EAE\u5EA6 0","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightColorIntensity1","label":"\u9AD8\u5149\u5E26\u4EAE\u5EA6 1","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightColorIntensity2","label":"\u9AD8\u5149\u5E26\u4EAE\u5EA6 2","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseTiling1","label":"\u9AD8\u5149\u566A\u58F0 1 \u5E73\u94FA","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseOffset1","label":"\u9AD8\u5149\u566A\u58F0 1 \u504F\u79FB","kind":"VALUE","size":1,"default":[0]},{"name":"HighlightNoiseIntensity1","label":"\u9AD8\u5149\u566A\u58F0 1 \u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseTiling2","label":"\u9AD8\u5149\u566A\u58F0 2 \u5E73\u94FA","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseOffset2","label":"\u9AD8\u5149\u566A\u58F0 2 \u504F\u79FB","kind":"VALUE","size":1,"default":[0]},{"name":"HighlightNoiseIntensity2","label":"\u9AD8\u5149\u566A\u58F0 2 \u5F3A\u5EA6","kind":"VALUE","size":1,"default":[0]},{"name":"HighlightUseNoise0","label":"\u9AD8\u5149\u5E26\u7528\u566A\u58F0 0","kind":"SWITCH","size":1,"default":[0]},{"name":"HighlightUseNoise1","label":"\u9AD8\u5149\u5E26\u7528\u566A\u58F0 1","kind":"SWITCH","size":1,"default":[0]},{"name":"HighlightUseNoise2","label":"\u9AD8\u5149\u5E26\u7528\u566A\u58F0 2","kind":"SWITCH","size":1,"default":[0]},{"name":"HighlightUseNoiseOffset0","label":"\u9AD8\u5149\u5E26\u7528\u566A\u58F0\u504F\u79FB 0","kind":"SWITCH","size":1,"default":[0]},{"name":"HighlightUseNoiseOffset1","label":"\u9AD8\u5149\u5E26\u7528\u566A\u58F0\u504F\u79FB 1","kind":"SWITCH","size":1,"default":[0]},{"name":"HighlightUseNoiseOffset2","label":"\u9AD8\u5149\u5E26\u7528\u566A\u58F0\u504F\u79FB 2","kind":"SWITCH","size":1,"default":[0]},{"name":"HighlightNoiseUpIntensity0","label":"\u9AD8\u5149\u5E26\u566A\u58F0\u4E0A\u5F3A\u5EA6 0","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseUpIntensity1","label":"\u9AD8\u5149\u5E26\u566A\u58F0\u4E0A\u5F3A\u5EA6 1","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseUpIntensity2","label":"\u9AD8\u5149\u5E26\u566A\u58F0\u4E0A\u5F3A\u5EA6 2","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseDownIntensity0","label":"\u9AD8\u5149\u5E26\u566A\u58F0\u4E0B\u5F3A\u5EA6 0","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseDownIntensity1","label":"\u9AD8\u5149\u5E26\u566A\u58F0\u4E0B\u5F3A\u5EA6 1","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightNoiseDownIntensity2","label":"\u9AD8\u5149\u5E26\u566A\u58F0\u4E0B\u5F3A\u5EA6 2","kind":"VALUE","size":1,"default":[1]},{"name":"HighlightUseMiddleMute0","label":"\u9AD8\u5149\u5E26\u4E2D\u6BB5\u9759\u97F3 0","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightUseMiddleMute1","label":"\u9AD8\u5149\u5E26\u4E2D\u6BB5\u9759\u97F3 1","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"HighlightUseMiddleMute2","label":"\u9AD8\u5149\u5E26\u4E2D\u6BB5\u9759\u97F3 2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"FurClusterMin","label":"\u6BDB\u7ED2\u5206\u533A\u4E0B\u754C","kind":"VALUE","size":1,"default":[5]},{"name":"FurClusterMax","label":"\u6BDB\u7ED2\u5206\u533A\u4E0A\u754C","kind":"VALUE","size":1,"default":[5]},{"name":"FurParallaxMin","label":"\u89C6\u5DEE\u6BDB\u7ED2\u5206\u533A\u4E0B\u754C","kind":"VALUE","size":1,"default":[5]},{"name":"FurParallaxMax","label":"\u89C6\u5DEE\u6BDB\u7ED2\u5206\u533A\u4E0A\u754C","kind":"VALUE","size":1,"default":[5]},{"name":"UseFurryParallax","label":"\u89C6\u5DEE\u6BDB\u7ED2","kind":"SWITCH","size":1,"default":[0]},{"name":"Height","label":"\u89C6\u5DEE\u6BDB\u7ED2\u9AD8\u5EA6","kind":"VALUE","size":1,"default":[0.07999999821186066]},{"name":"Plane","label":"\u89C6\u5DEE\u6BDB\u7ED2\u57FA\u51C6\u9762","kind":"VALUE","size":1,"default":[0]},{"name":"HeightMapSize","label":"\u89C6\u5DEE\u6BDB\u7ED2\u5E73\u94FA","kind":"VALUE","size":1,"default":[1]},{"name":"MinStep","label":"\u89C6\u5DEE\u6BDB\u7ED2\u6700\u5C11\u5C42\u6570","kind":"VALUE","size":1,"default":[8]},{"name":"MaxStep","label":"\u89C6\u5DEE\u6BDB\u7ED2\u6700\u591A\u5C42\u6570","kind":"VALUE","size":1,"default":[8]},{"name":"ParallaxRimFadeMin","label":"\u89C6\u5DEE\u6BDB\u7ED2\u8F6E\u5ED3\u6DE1\u51FA\u4E0B\u754C","kind":"VALUE","size":1,"default":[0]},{"name":"ParallaxRimFadeMax","label":"\u89C6\u5DEE\u6BDB\u7ED2\u8F6E\u5ED3\u6DE1\u51FA\u4E0A\u754C","kind":"VALUE","size":1,"default":[0]},{"name":"FlowMapInt","label":"\u89C6\u5DEE\u6BDB\u7ED2\u6D41\u5411\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[0]},{"name":"FlowmapScale","label":"\u89C6\u5DEE\u6BDB\u7ED2\u6D41\u5411\u5E73\u94FA","kind":"VALUE","size":1,"default":[1]},{"name":"FurRootInv","label":"\u89C6\u5DEE\u6BDB\u7ED2\u5F2F\u6298\u5206\u914D","kind":"VALUE","size":1,"default":[1]},{"name":"FurRangeStep","label":"\u6BDB\u7ED2\u671D\u5411\u659C\u7387","kind":"VALUE","size":1,"default":[2]},{"name":"FurRangeStepOffset","label":"\u6BDB\u7ED2\u671D\u5411\u504F\u79FB","kind":"VALUE","size":1,"default":[0]},{"name":"FurAlphaIntensity","label":"\u6BDB\u7ED2\u4E0D\u900F\u660E\u5F3A\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"FurBlendUseVertexRFade","label":"\u6BDB\u7ED2\u6309\u9876\u70B9\u8272\u6E10\u9690","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"FurBlendIntensity","label":"\u6BDB\u7ED2\u6DF1\u5EA6\u504F\u79FB","kind":"VALUE","size":1,"default":[0]},{"name":"FurTexU","label":"\u6BDB\u7ED2\u906E\u7F69 U \u5E73\u94FA","kind":"VALUE","size":1,"default":[1]},{"name":"Length","label":"\u6BDB\u7ED2\u5916\u63A8\u957F\u5EA6","kind":"VALUE","size":1,"default":[0]},{"name":"FurOffset","label":"\u6BDB\u7ED2\u5916\u63A8\u8D77\u70B9","kind":"VALUE","size":1,"default":[-0.10000000149011612]},{"name":"UseVertexColor","label":"\u5916\u63A8\u957F\u5EA6\u6309\u9876\u70B9\u8272","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_EngineMetresPerUnit","label":"\u6E90\u5F15\u64CE\u4E16\u754C\u5355\u4F4D(\u7C73)","kind":"VALUE","size":1,"default":[0.009999999776482582]},{"name":"_IsSceneEffect","label":"Scene Effect (color grade)","kind":"SWITCH","size":1,"default":[0]},{"name":"_Responsive","label":"Responsive","kind":"SWITCH","size":1,"default":[0]},{"name":"_EnableTransparentMV","label":"Enable Transparent MV","kind":"SWITCH","size":1,"default":[0]},{"name":"_MainTexMipmapBias","label":"MainTex Mipmap Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_RowsColumns","label":"Frame Rows/Columns","kind":"VECTOR","size":4,"default":[1,1,0,0]},{"name":"_Frame","label":"Frame Index","kind":"VALUE","size":1,"default":[1]},{"name":"_ScaleOffset","label":"Frame Scale/Offset","kind":"VECTOR","size":4,"default":[1,1,0,0]},{"name":"_CutsceneBaseColor","label":"Cutscene Base Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_LightRange1","label":"Light Range 1","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_LightRange2","label":"Light Range 2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_LeftFadeRange","label":"Left Fade Range","kind":"VALUE","size":1,"default":[0.10000000149011612]},{"name":"_RightFadeRange","label":"Right Fade Range","kind":"VALUE","size":1,"default":[0.10000000149011612]},{"name":"_ColorOption","label":"Color Option (\u4EAE/\u6697)","kind":"SWITCH","size":1,"default":[0]},{"name":"_ShapeOption","label":"Shape Option (\u7EB5\u5411/\u5F84\u5411)","kind":"SWITCH","size":1,"default":[0]},{"name":"_DepthFadePosition","label":"Depth Fade Position","kind":"VALUE","size":1,"default":[0]},{"name":"_NearFadeIntensity","label":"Near Fade Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_DepthFadeRange","label":"Depth Fade Range","kind":"VALUE","size":1,"default":[1]},{"name":"_LightIntensity","label":"Light Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_DarkIntensity","label":"Dark Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_EnableuseInvertFade","label":"Use Invert Fade","kind":"SWITCH","size":1,"default":[0]},{"name":"_RainColor","label":"Rain Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_RainTex0_ST","label":"RainTex0 Tiling/Offset","kind":"VECTOR","size":4,"default":[1,1,0,1]},{"name":"_DecalBaseColor","label":"Decal Base Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_EdgeFadeX","label":"Decal Edge Fade X","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0]},{"name":"_EdgeFadeZ","label":"Decal Edge Fade Z","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0]},{"name":"_EdgeFadeIntensity","label":"Decal Edge Fade Intensity","kind":"VALUE","size":1,"default":[0.009999999776482582]},{"name":"_HeightFade","label":"Decal Height Fade","kind":"VALUE","size":1,"default":[1]},{"name":"_HeightFadeOffset","label":"Decal Height Fade Offset","kind":"VALUE","size":1,"default":[0]},{"name":"_MainCustomData","label":"Decal Main Custom Data","kind":"VALUE","size":1,"default":[0]},{"name":"_MaskCustomData","label":"Decal Mask Custom Data","kind":"VALUE","size":1,"default":[0]},{"name":"_FogColor","label":"Fog Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_FogDensity","label":"Fog Density","kind":"VALUE","size":1,"default":[1]},{"name":"_FogExponent","label":"Fog Exponent","kind":"VALUE","size":1,"default":[1]},{"name":"_CubEdgeFade","label":"Fog Cube Edge Fade","kind":"VALUE","size":1,"default":[1]},{"name":"_HeightOffset","label":"Fog Height Offset","kind":"VALUE","size":1,"default":[0]},{"name":"_HeightFalloff","label":"Fog Height Falloff","kind":"VALUE","size":1,"default":[0]},{"name":"_VerticalFalloffMin","label":"Fog Vertical Falloff Min","kind":"VALUE","size":1,"default":[0]},{"name":"_VerticalFalloffMax","label":"Fog Vertical Falloff Max","kind":"VALUE","size":1,"default":[0]},{"name":"_BlendDisableVertColor","label":"BlendTex Disable Vertex Color","kind":"SWITCH","size":1,"default":[0]},{"name":"_InkSimulationWorldToUV","label":"Ink Simulation World To UV","kind":"VECTOR","size":4,"default":[0,0,0,1]},{"name":"_MaskTexUseInkSimulation","label":"MaskTex Use Ink Simulation","kind":"SWITCH","size":1,"default":[0]},{"name":"_DisturbTex1UseInkSimulation","label":"DisturbTex1 Use Ink Simulation","kind":"SWITCH","size":1,"default":[0]},{"name":"_DitherTilling","label":"Dither Tilling","kind":"VALUE","size":1,"default":[1]},{"name":"_DitherAlphaExp","label":"Dither Alpha Exp","kind":"VALUE","size":1,"default":[1]},{"name":"_DitherAlphaMode","label":"Dither Alpha Mode (0 = Add, 1 = Multiply)","kind":"VALUE","size":1,"default":[0]},{"name":"_DitherAlphaEdge","label":"Dither Alpha Edge","kind":"SLIDER","size":1,"min":0,"max":0.1,"default":[0.03999999910593033]},{"name":"_DirSpecularStrength","label":"Dir Specular Strength","kind":"VALUE","size":1,"default":[0]},{"name":"_FresnelStrength","label":"Fresnel Strength","kind":"VALUE","size":1,"default":[1]},{"name":"_IndirectSaturation","label":"Indirect Saturation","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_UseFresnelAsOpacity","label":"Use Fresnel As Opacity","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseVertexColorAsOpacity","label":"Use Vertex Color As Opacity","kind":"SWITCH","size":1,"default":[0]},{"name":"_WaterRandomUV","label":"Water Random UV","kind":"SWITCH","size":1,"default":[0]},{"name":"_RefractThickness","label":"Refract Thickness","kind":"VALUE","size":1,"default":[0]},{"name":"_AbsorptionRange","label":"Absorption Range","kind":"VALUE","size":1,"default":[1]},{"name":"_EnvLightSaturation","label":"Env Light Saturation","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_LightMapUVRotate","label":"Lightmap UV Rotate (\u5EA6)","kind":"VALUE","size":1,"default":[0]},{"name":"_LightMapUVSpeed","label":"Lightmap UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_LightMapControls","label":"Lightmap Tiling/Offset","kind":"VECTOR","size":4,"default":[1,1,0,0]},{"name":"_Intensity","label":"Refract Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_RefractIsNormal","label":"Refract Tex Is Normal","kind":"SWITCH","size":1,"default":[0]},{"name":"_Bi_Refract","label":"Bi Refract","kind":"VALUE","size":1,"default":[0]},{"name":"_RefractDir","label":"Refract Direction","kind":"VECTOR","size":4,"default":[1,1,0,0]},{"name":"_RefractTexUVRotate","label":"RefractTex UV Rotate (\u5EA6)","kind":"VALUE","size":1,"default":[0]},{"name":"_RefractUVSpeed","label":"RefractTex UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_MaskTexUseRefract","label":"MaskTex Use Refract","kind":"VALUE","size":1,"default":[0]},{"name":"_Use_Global_Appear","label":"\u5168\u5C40\u663E\u9690","kind":"SWITCH","size":1,"default":[0]},{"name":"_DoughnutRadius","label":"\u73AF\u5F62\u534A\u5F84","kind":"SLIDER","size":1,"min":0,"max":50,"default":[10]},{"name":"_DoughnutWidth","label":"\u73AF\u5F62\u5BBD\u5EA6","kind":"SLIDER","size":1,"min":0,"max":20,"default":[5]},{"name":"_UseRefract","label":"Use Refract","kind":"SWITCH","size":1,"default":[0]},{"name":"_RefractTint","label":"\u6298\u5C04\u67D3\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_IoR","label":"\u6298\u5C04\u7387","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0.800000011920929]},{"name":"_RefractBrightness","label":"\u6298\u5C04\u4EAE\u5EA6","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_RefractionContribution","label":"\u6298\u5C04\u8D21\u732E","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_UseSubsurface","label":"Use Subsurface","kind":"SWITCH","size":1,"default":[0]},{"name":"_SubsurfaceIndirect","label":"\u6B21\u8868\u9762\u5BF9\u95F4\u63A5\u5149\u5F71\u54CD","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_SubsurfaceColor","label":"\u6B21\u8868\u9762\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_SubsurfaceHue","label":"\u6B21\u8868\u9762\u8272\u76F8","kind":"VALUE","size":1,"default":[1]},{"name":"_SubsurfaceSaturation","label":"\u6B21\u8868\u9762\u9971\u548C","kind":"VALUE","size":1,"default":[1]},{"name":"_SubsurfaceValue","label":"\u6B21\u8868\u9762\u660E\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"_RandomStartByPos","label":"random Start By Position","kind":"SWITCH","size":1,"default":[0]},{"name":"_SequenceOpacityScale","label":"\u5E8F\u5217\u5E27\u4E0D\u900F\u660E\u5EA6Scale","kind":"SLIDER","size":1,"min":0,"max":3,"default":[1]},{"name":"_SequenceOpacitySdf","label":"\u5E8F\u5217\u5E27\u4E0D\u900F\u660E\u5EA6\u8303\u56F4\u63A7\u5236","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_SequenceBaseColorMapTint","label":"\u5E8F\u5217\u5E27\u989C\u8272\u56FE\u8272\u76F8","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_SequenceBaseColorSdf","label":"\u5E8F\u5217\u5E27BaseColor\u8303\u56F4\u63A7\u5236","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_MotionVectorDistortionStrength","label":"Motion Vector Distortion Strength","kind":"SLIDER","size":1,"min":0,"max":0.2,"default":[0]},{"name":"_FrameNormalScale","label":"\u5E8F\u5217\u5E27\u6CD5\u7EBF\u56FEScale","kind":"SLIDER","size":1,"min":0,"max":3,"default":[1]},{"name":"_NormalMapUseDisturb","label":"Normal Map Use Disturb","kind":"SWITCH","size":1,"default":[1]},{"name":"_DissolveTexUseDisturb","label":"Dissolve Tex Use Disturb","kind":"SLIDER","size":1,"min":0,"max":5,"default":[0]},{"name":"_DissolveUVWeights","label":"Dissolve UV Weights","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_DissolveUVSpeed","label":"Dissolve UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_DissolveUVRotateMat","label":"Dissolve UV Rotate Mat","kind":"VECTOR","size":4,"default":[1,0,0,1]},{"name":"_UseVertexColorAlphaAsDissolveWeight","label":"\u4F7F\u7528\u9876\u70B9\u8272\u4F5C\u4E3A\u6EB6\u89E3weight","kind":"SWITCH","size":1,"default":[0]},{"name":"_DissolveVertexChannel","label":"\u6EB6\u89E3Weight\u4F7F\u7528\u9876\u70B9\u8272\u901A\u9053 (0=R 1=A)","kind":"VALUE","size":1,"default":[0]},{"name":"_DissolveVertexRange","label":"\u9876\u70B9\u8272\u6EB6\u89E3\u8303\u56F4\u901A\u9053","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_UseIceGrow","label":"Use Ice Grow","kind":"SWITCH","size":1,"default":[0]},{"name":"_CharHeight","label":"Char Height","kind":"VALUE","size":1,"default":[1.5]},{"name":"_GrowStart","label":"Grow Start","kind":"VALUE","size":1,"default":[0]},{"name":"_GrowSchedule","label":"Grow Schedule","kind":"VALUE","size":1,"default":[0]},{"name":"_UseVertexOffset","label":"Use Vertex Offset","kind":"SWITCH","size":1,"default":[0]},{"name":"_OffsetSpeed","label":"Offset Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_OffsetDir","label":"Offset Dir (xyz \u8F74, w \u6309\u81EA\u5B9A\u4E49\u6570\u636E)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_OffsetSwitchDir","label":"Offset Dir Switch (0=\u7269\u4F53 1=\u4E16\u754C 2=\u6CD5\u7EBF)","kind":"VALUE","size":1,"default":[0]},{"name":"_OffsetIntensity","label":"Offset Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_Bi_Offset","label":"Bi Offset","kind":"SWITCH","size":1,"default":[0]},{"name":"_OffsetUVSet","label":"Offset UV Set (0=UV0 1=UV1)","kind":"VALUE","size":1,"default":[0]},{"name":"_UseVertexOffsetMask","label":"Use Vertex Offset Mask","kind":"SWITCH","size":1,"default":[0]},{"name":"_OffsetMaskSpeed","label":"Offset Mask Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_OffsetMaskPower","label":"Offset Mask Power","kind":"VALUE","size":1,"default":[1]},{"name":"_UseVertexOffsetCharPos","label":"Use Vertex Offset CharPos","kind":"SWITCH","size":1,"default":[0]},{"name":"_VertexOffsetDoughnutIntensity","label":"Doughnut Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_VertexOffsetDoughnutRadius","label":"Doughnut Radius","kind":"SLIDER","size":1,"min":0,"max":100,"default":[5]},{"name":"_VertexOffsetDoughnutWidth","label":"Doughnut Width","kind":"SLIDER","size":1,"min":0,"max":200,"default":[10]},{"name":"_HoudiniFPS","label":"Houdini FPS","kind":"VALUE","size":1,"default":[0]},{"name":"_UseColorGradient","label":"Use Color Gradient","kind":"SWITCH","size":1,"default":[0]},{"name":"_ColorTop","label":"Color Top","kind":"COLOR","size":4,"default":[0,0,0,1],"gamma":true},{"name":"_ColorBottom","label":"Color Bottom","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ColorGradientCenter","label":"Color Gradient Center","kind":"VALUE","size":1,"default":[0]},{"name":"_ColorGradientRange","label":"Color Gradient Range","kind":"VALUE","size":1,"default":[0.20000000298023224]},{"name":"_UseCubeMap","label":"Use CubeMap","kind":"SWITCH","size":1,"default":[0]},{"name":"_CubeMapColor","label":"Cube Map Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_UseMain2","label":"Use Main 2","kind":"SWITCH","size":1,"default":[0]},{"name":"_MainTex2Color","label":"Main Tex 2 Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_UseMainTex2AsAlpha","label":"Use MainTex2 As Alpha","kind":"SWITCH","size":1,"default":[1]},{"name":"_MainTex2UVSpeed","label":"MainTex2 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_MainTex2UVRotateMat","label":"MainTex2 UV Rotate Mat","kind":"VECTOR","size":4,"default":[1,0,0,1]},{"name":"_MainTex2UVWeights","label":"MainTex2 UV Weights","kind":"VECTOR","size":4,"default":[1,0,0,0]},{"name":"_MainTex2BlendMode","label":"MainTex2 Blend (0=\u4E58 1=\u52A0)","kind":"VALUE","size":1,"default":[0]},{"name":"_UseInkSimulation","label":"Use Ink Simulation","kind":"SWITCH","size":1,"default":[0]},{"name":"_InkColor","label":"Ink Color","kind":"COLOR","size":4,"default":[0,0,0,1],"gamma":true},{"name":"_InkStrength","label":"Ink Strength","kind":"VALUE","size":1,"default":[15]},{"name":"_InkMaxAlpha","label":"Ink Max Alpha","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.800000011920929]},{"name":"_InkDisturbOffset","label":"Ink Disturb Offset","kind":"SLIDER","size":1,"min":0,"max":0.1,"default":[0.02500000037252903]},{"name":"_UseFlowmap","label":"Use Flowmap","kind":"SWITCH","size":1,"default":[0]},{"name":"_FlowmapUVPannerSpeed","label":"Flowmap UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_FlowmapUVRotate","label":"Flowmap UV Rotate (\u5EA6)","kind":"VALUE","size":1,"default":[0]},{"name":"_FlowmapStrength","label":"Flowmap Disturb Strength","kind":"VALUE","size":1,"default":[0.5]},{"name":"_FlowmapSpeed","label":"Flowmap Velocity","kind":"VALUE","size":1,"default":[0.10000000149011612]},{"name":"_MainTexUseFlowmap","label":"MainTex Use Flowmap","kind":"SWITCH","size":1,"default":[1]},{"name":"_UseAirWallTexture","label":"Use AirWall Texture","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseAirWallTexAsAlpha","label":"Use AirWall Tex As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_AirWallUVWeights","label":"AirWall UV Weights","kind":"VECTOR","size":4,"default":[1,0,0,0]},{"name":"_UseHeightColorGradient","label":"Use Height Color Gradient","kind":"SWITCH","size":1,"default":[0]},{"name":"_HeightColorGradientColor","label":"Height Gradient Color","kind":"HDRCOLOR","size":4,"default":[1.850000023841858,1.850000023841858,1.850000023841858,1],"gamma":true},{"name":"_HeightColorGradientLocationDown","label":"Height Gradient Location Down","kind":"VALUE","size":1,"default":[1.4199999570846558]},{"name":"_HeightColorGradientLocationTop","label":"Height Gradient Location Top","kind":"VALUE","size":1,"default":[100]},{"name":"_HeightColorGradientSmooth","label":"Height Gradient Smooth","kind":"SLIDER","size":1,"min":0.01,"max":0.8,"default":[0.7799999713897705]},{"name":"_HeightColorGradientAffectColor","label":"Height Gradient Affect Color","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_HeightColorGradientAffectAlpha","label":"Height Gradient Affect Alpha","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_HeightColorGradientColor2","label":"Height Gradient Color2","kind":"HDRCOLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_HeightColorGradientLocationDown2","label":"Height Gradient Location Down2","kind":"VALUE","size":1,"default":[-10]},{"name":"_HeightColorGradientLocationTop2","label":"Height Gradient Location Top2","kind":"VALUE","size":1,"default":[0.30000001192092896]},{"name":"_HeightColorGradientSmooth2","label":"Height Gradient Smooth2","kind":"SLIDER","size":1,"min":0.01,"max":0.8,"default":[0.30000001192092896]},{"name":"_HeightColorGradientAffectColor2","label":"Height Gradient Affect Color2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_HeightColorGradientAffectAlpha2","label":"Height Gradient Affect Alpha2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_UseUnderGround","label":"Use Under Ground","kind":"SWITCH","size":1,"default":[0]},{"name":"_UnderGroundPlaneYOffset","label":"Under Ground Plane Y Offset","kind":"VALUE","size":1,"default":[0]},{"name":"_UnderGroundFadeDistance","label":"Under Ground Fade Distance","kind":"VALUE","size":1,"default":[1]},{"name":"_UseMainTex","label":"Use Main Tex","kind":"SWITCH","size":1,"default":[0]},{"name":"_SixWayLightMap","label":"Six Way Light Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseEmissiveRampMap","label":"Use Emissive Ramp","kind":"SWITCH","size":1,"default":[0]},{"name":"_LightMapUseDisturb","label":"LightMap Use Disturb","kind":"SWITCH","size":1,"default":[1]},{"name":"_DisturbUVSpeed2","label":"Disturb2 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_DisturbUIntensity2","label":"Disturb2 U Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_DisturbVIntensity2","label":"Disturb2 V Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_DisturbTex2Normal","label":"Disturb2 Is Normal","kind":"SWITCH","size":1,"default":[0]},{"name":"_WeightTexUVSpeed","label":"WeightTex UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_WeightTexUVRotate","label":"WeightTex UV Rotate (\u5EA6)","kind":"VALUE","size":1,"default":[0]},{"name":"_GameTimeAtFirstFrame","label":"Game Time At First Frame","kind":"VALUE","size":1,"default":[0]},{"name":"_DisplayFrame","label":"Display Frame","kind":"VALUE","size":1,"default":[0]},{"name":"_PlaybackSpeed","label":"Playback Speed","kind":"VALUE","size":1,"default":[0]},{"name":"_LightMapFPS","label":"LightMap FPS","kind":"VALUE","size":1,"default":[0]},{"name":"_SequenceFrameSize","label":"Sequence Frame Size (xy)","kind":"VECTOR","size":4,"default":[1,1,0,0]},{"name":"_UseRBOffset","label":"Use RBOffset","kind":"SWITCH","size":1,"default":[0]},{"name":"_RBIntensity","label":"RBOffset Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_RBOffset","label":"RBOffset","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_GOffset","label":"Offset Of G Channel","kind":"SLIDER","size":1,"min":-2,"max":2,"default":[-1]},{"name":"_RBMainColorMask","label":"Main Color Mask","kind":"COLOR","size":4,"default":[1,0,0,1],"gamma":true},{"name":"_RBOffsetColorMask","label":"RBOffset Color Mask","kind":"COLOR","size":4,"default":[0,1,1,1],"gamma":true},{"name":"_RBOffset1ColorMask","label":"RBOffset1 Color Mask","kind":"COLOR","size":4,"default":[0,1,0,1],"gamma":true},{"name":"_RBOffset2ColorMask","label":"RBOffset2 Color Mask","kind":"COLOR","size":4,"default":[0,0,1,1],"gamma":true},{"name":"_UseMainTexAsMask","label":"Use MainTex As Mask","kind":"SWITCH","size":1,"default":[0]},{"name":"_SaturationValue","label":"Saturation","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_ColorBlendIntensity","label":"Color Blend Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_ColorIntensity","label":"Color Intensity","kind":"VALUE","size":1,"default":[1]},{"name":"_SpreadUVSpeed","label":"Spread UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SpreadUVRotate","label":"Spread UV Rotate (\u5EA6)","kind":"VALUE","size":1,"default":[0]},{"name":"_SpreadScheduleOffset","label":"Spread Schedule Offset","kind":"VALUE","size":1,"default":[0]},{"name":"_SpreadRange","label":"Spread Range","kind":"VALUE","size":1,"default":[1]},{"name":"_SpreadFlip","label":"Spread Flip","kind":"SWITCH","size":1,"default":[0]},{"name":"_SpreadAlphaCurve","label":"Spread Alpha Curve","kind":"VALUE","size":1,"default":[1]},{"name":"_RadialBlurIntensity","label":"Radial Blur Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_Power","label":"Radial Blur Power","kind":"VALUE","size":1,"default":[1]},{"name":"_CanterOffset","label":"Radial Blur Center Offset","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_MainTexUVRotate","label":"MainTex UV Rotate (\u5EA6)","kind":"VALUE","size":1,"default":[0]},{"name":"_MaskTexUVRotate","label":"MaskTex UV Rotate (\u5EA6)","kind":"VALUE","size":1,"default":[0]},{"name":"_Color","label":"Smoke Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_Opacity","label":"Smoke Opacity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_HighLightColor","label":"Wire Highlight Color","kind":"HDRCOLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_HighLightPos","label":"Wire Highlight Pos","kind":"VALUE","size":1,"default":[0]},{"name":"_HighLightOffset","label":"Wire Highlight Width","kind":"VALUE","size":1,"default":[0]},{"name":"_UVSet","label":"Ice UV Set (1=\u7269\u4F53\u8F74\u6295\u5F71)","kind":"SWITCH","size":1,"default":[0]},{"name":"_TilingOffset","label":"Ice Tiling Offset","kind":"VECTOR","size":4,"default":[1,1,0,0]},{"name":"_EmmissiveColor","label":"Ice Emissive Color","kind":"HDRCOLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_UseAnimBreathing","label":"Use Anim Breathing","kind":"SWITCH","size":1,"default":[0]},{"name":"_BreathingSpeed","label":"Breathing Speed","kind":"VALUE","size":1,"default":[1]},{"name":"_BreathingPower","label":"Breathing Power","kind":"VALUE","size":1,"default":[1]},{"name":"_BreathingMinAlpha","label":"Breathing Min Alpha","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_UseEdgeColor","label":"Use Edge Color","kind":"SWITCH","size":1,"default":[0]},{"name":"_EdgeColor","label":"Edge Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_EdgeDistance","label":"Edge Distance","kind":"VALUE","size":1,"default":[1]},{"name":"_EdgeDistanceOffset","label":"Edge Distance Offset","kind":"VALUE","size":1,"default":[0]},{"name":"_EdgeColorMode","label":"Edge Color Mode (1 = \u52A0, 0 = \u4E58)","kind":"VALUE","size":1,"default":[1]},{"name":"_LocalPivortSpace","label":"Local Pivot Space (\u5C4F\u5E55 uv \u6362\u6210\u8F74\u70B9\u5C40\u90E8)","kind":"SWITCH","size":1,"default":[0]},{"name":"_UsePosYAsScreenV","label":"Use PosY As Screen V (\u4E16\u754C Y \u5F53 V)","kind":"SWITCH","size":1,"default":[0]},{"name":"_ScreenUVUseDepth","label":"Screen UV Use Depth (\u5C4F\u5E55\u5750\u6807\u53D7\u76F8\u673A\u8DDD\u79BB\u5F71\u54CD)","kind":"SWITCH","size":1,"default":[1]},{"name":"_SoftDistance","label":"Soft Distance","kind":"VALUE","size":1,"default":[1]},{"name":"_SoftBias","label":"Soft Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_MainSwitchUV","label":"Main Switch UV","kind":"VALUE","size":1,"default":[0]},{"name":"_MaskSwitchUV","label":"Mask Switch UV","kind":"VALUE","size":1,"default":[0]},{"name":"_DissolveSwitchUV","label":"Dissolve Switch UV","kind":"VALUE","size":1,"default":[0]},{"name":"_DisturbSwitchUV","label":"Disturb Switch UV","kind":"VALUE","size":1,"default":[0]},{"name":"_BlendSwitchUV","label":"Blend Switch UV","kind":"VALUE","size":1,"default":[0]},{"name":"_DissolveDir","label":"Ice Dissolve Direction","kind":"VECTOR","size":4,"default":[0,1,0,0]},{"name":"_CutOffPosY","label":"CutOff Pos","kind":"VALUE","size":1,"default":[0]},{"name":"_CutOffSpace","label":"CutOff Space (1=\u4E16\u754C 0=\u7269\u4F53)","kind":"SWITCH","size":1,"default":[1]},{"name":"_CutOffWidth","label":"CutOff Width","kind":"VALUE","size":1,"default":[0]},{"name":"_CutOffTransition","label":"CutOff Transition","kind":"VALUE","size":1,"default":[1]},{"name":"_CutOffAffectOpacity","label":"CutOff Affect Opacity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_CutOffDirection","label":"CutOff Direction","kind":"VECTOR","size":4,"default":[0,1,0,0]},{"name":"_UseLighting","label":"Use Lighting (\u5EF6\u8FDF\u817F\u5149\u7167\u5360\u6BD4)","kind":"SWITCH","size":1,"default":[0]},{"name":"_Roughness","label":"Roughness","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_ExpThreshold","label":"Exp Threshold","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_ExpIntensity","label":"Exp Intensity","kind":"SLIDER","size":1,"min":0,"max":100,"default":[0]},{"name":"_UseParticleDisturb","label":"Use Particle Disturb","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseDissolve","label":"Use Dissolve","kind":"SWITCH","size":1,"default":[0]},{"name":"_DissolveScheduleOffset","label":"Dissolve Schedule Offset","kind":"VALUE","size":1,"default":[0]},{"name":"_DissolveEdgeSharp","label":"Dissolve Edge Sharp","kind":"VALUE","size":1,"default":[1]},{"name":"_DissolveEmissiveEdge","label":"Dissolve Emissive Edge","kind":"VALUE","size":1,"default":[0]},{"name":"_DissolveEmissiveColor","label":"Dissolve Emissive Color","kind":"HDRCOLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_DissolveUseWeight","label":"Dissolve Use Weight","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseWeightTex","label":"Use Weight Tex","kind":"SWITCH","size":1,"default":[0]},{"name":"_WeightTexIntensity","label":"Weight Tex Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_UseBright","label":"Use Bright","kind":"SWITCH","size":1,"default":[0]},{"name":"_BrightCenter","label":"Bright Center (w = \u7528\u6750\u8D28\u70B9)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_BrightColor","label":"Bright Color","kind":"HDRCOLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_BrightType","label":"Bright Type (0=Radius 1=ScanLine)","kind":"VALUE","size":1,"default":[0]},{"name":"_BrightUseVertColor","label":"Bright Use Vert Color","kind":"SWITCH","size":1,"default":[0]},{"name":"_CharacterHeight","label":"Character Height","kind":"VALUE","size":1,"default":[0]},{"name":"_ScanFillColor","label":"Scan Fill Color","kind":"HDRCOLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_ScanLineSchedule","label":"Scan Line Schedule","kind":"VALUE","size":1,"default":[0]},{"name":"_OuterRadius","label":"Outer Radius","kind":"VALUE","size":1,"default":[0]},{"name":"_InnerRadius","label":"Inner Radius","kind":"VALUE","size":1,"default":[0]},{"name":"_DistortAlpha","label":"Distort Alpha","kind":"VALUE","size":1,"default":[1]},{"name":"_DistortIntensity","label":"Distort Intensity","kind":"VALUE","size":1,"default":[0]},{"name":"_DistortOnEdge","label":"Distort On Edge","kind":"VALUE","size":1,"default":[0]},{"name":"_DistortScale","label":"Distort Scale","kind":"VALUE","size":1,"default":[1]},{"name":"_DistortSpeed","label":"Distort Speed","kind":"VALUE","size":1,"default":[0]},{"name":"_UseSampleTex0","label":"Use SampleTex0","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseSampleTex0AsAlpha","label":"SampleTex0 As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_SampleTex0MipmapBias","label":"SampleTex0 Mipmap Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex0UVSpeed","label":"SampleTex0 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SampleTex0UseWeight0","label":"SampleTex0 \u2192 \u6270\u52A8","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex0UseWeight2","label":"SampleTex0 \u2192 \u6EB6\u89E3\u6743\u91CD","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex0UseWeight3","label":"SampleTex0 \u2192 \u906E\u7F69","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex0UseWeight4","label":"SampleTex0 \u2192 \u53E0\u8272","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex0UseWeight5","label":"SampleTex0 \u2192 \u6EB6\u89E3\u6392\u7A0B","kind":"VALUE","size":1,"default":[0]},{"name":"_UseSampleTex1","label":"Use SampleTex1","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseSampleTex1AsAlpha","label":"SampleTex1 As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_SampleTex1MipmapBias","label":"SampleTex1 Mipmap Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex1UVSpeed","label":"SampleTex1 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SampleTex1UseWeight0","label":"SampleTex1 \u2192 \u6270\u52A8","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex1UseWeight2","label":"SampleTex1 \u2192 \u6EB6\u89E3\u6743\u91CD","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex1UseWeight3","label":"SampleTex1 \u2192 \u906E\u7F69","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex1UseWeight4","label":"SampleTex1 \u2192 \u53E0\u8272","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex1UseWeight5","label":"SampleTex1 \u2192 \u6EB6\u89E3\u6392\u7A0B","kind":"VALUE","size":1,"default":[0]},{"name":"_UseSampleTex2","label":"Use SampleTex2","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseSampleTex2AsAlpha","label":"SampleTex2 As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_SampleTex2MipmapBias","label":"SampleTex2 Mipmap Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex2UVSpeed","label":"SampleTex2 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SampleTex2UseWeight0","label":"SampleTex2 \u2192 \u6270\u52A8","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex2UseWeight2","label":"SampleTex2 \u2192 \u6EB6\u89E3\u6743\u91CD","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex2UseWeight3","label":"SampleTex2 \u2192 \u906E\u7F69","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex2UseWeight4","label":"SampleTex2 \u2192 \u53E0\u8272","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex2UseWeight5","label":"SampleTex2 \u2192 \u6EB6\u89E3\u6392\u7A0B","kind":"VALUE","size":1,"default":[0]},{"name":"_UseSampleTex3","label":"Use SampleTex3","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseSampleTex3AsAlpha","label":"SampleTex3 As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_SampleTex3MipmapBias","label":"SampleTex3 Mipmap Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex3UVSpeed","label":"SampleTex3 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SampleTex3UseWeight0","label":"SampleTex3 \u2192 \u6270\u52A8","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex3UseWeight2","label":"SampleTex3 \u2192 \u6EB6\u89E3\u6743\u91CD","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex3UseWeight3","label":"SampleTex3 \u2192 \u906E\u7F69","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex3UseWeight4","label":"SampleTex3 \u2192 \u53E0\u8272","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex3UseWeight5","label":"SampleTex3 \u2192 \u6EB6\u89E3\u6392\u7A0B","kind":"VALUE","size":1,"default":[0]},{"name":"_UseSampleTex4","label":"Use SampleTex4","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseSampleTex4AsAlpha","label":"SampleTex4 As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_SampleTex4MipmapBias","label":"SampleTex4 Mipmap Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex4UVSpeed","label":"SampleTex4 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SampleTex4UseWeight0","label":"SampleTex4 \u2192 \u6270\u52A8","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex4UseWeight2","label":"SampleTex4 \u2192 \u6EB6\u89E3\u6743\u91CD","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex4UseWeight3","label":"SampleTex4 \u2192 \u906E\u7F69","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex4UseWeight4","label":"SampleTex4 \u2192 \u53E0\u8272","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex4UseWeight5","label":"SampleTex4 \u2192 \u6EB6\u89E3\u6392\u7A0B","kind":"VALUE","size":1,"default":[0]},{"name":"_UseSampleTex5","label":"Use SampleTex5","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseSampleTex5AsAlpha","label":"SampleTex5 As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_SampleTex5MipmapBias","label":"SampleTex5 Mipmap Bias","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex5UVSpeed","label":"SampleTex5 UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_SampleTex5UseWeight0","label":"SampleTex5 \u2192 \u6270\u52A8","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex5UseWeight2","label":"SampleTex5 \u2192 \u6EB6\u89E3\u6743\u91CD","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex5UseWeight3","label":"SampleTex5 \u2192 \u906E\u7F69","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex5UseWeight4","label":"SampleTex5 \u2192 \u53E0\u8272","kind":"VALUE","size":1,"default":[0]},{"name":"_SampleTex5UseWeight5","label":"SampleTex5 \u2192 \u6EB6\u89E3\u6392\u7A0B","kind":"VALUE","size":1,"default":[0]},{"name":"_ConeParams0","label":"Cone Params 0 (xy \u8D77\u6B62\u534A\u5F84, zw \u9525\u5761 cos/sin)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_ConeParams1","label":"Cone Params 1 (xyz \u672C\u5730\u524D\u5411, w \u753B\u7AEF\u76D6)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_DistanceFallOff","label":"Distance Fall Off (x \u8D77, y \u6B62, z \u51E0\u4F55\u957F\u5EA6, w \u8D77\u70B9\u6E10\u5165)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_TiltVectorX","label":"Tilt Vector X","kind":"VALUE","size":1,"default":[0]},{"name":"_TiltVectorY","label":"Tilt Vector Y","kind":"VALUE","size":1,"default":[0]},{"name":"_ConeGeomProps","label":"Cone Geom Props (\u865A\u9876\u70B9\u6CBF\u8F74\u7684\u504F\u79FB)","kind":"VALUE","size":1,"default":[0]},{"name":"_ColorFlat","label":"Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_AlphaInside","label":"Alpha Inside","kind":"VALUE","size":1,"default":[1]},{"name":"_AlphaOutside","label":"Alpha Outside","kind":"VALUE","size":1,"default":[1]},{"name":"_DistanceCamClipping","label":"Camera Clipping Distance","kind":"VALUE","size":1,"default":[0.5]},{"name":"_DistanceFadeStart","label":"Distance Fade Start","kind":"SLIDER","size":1,"min":0.001,"max":3000,"default":[0.0010000000474974513]},{"name":"_DistanceFadeEnd","label":"Distance Fade End","kind":"SLIDER","size":1,"min":0.001,"max":3000,"default":[0.0010000000474974513]},{"name":"_DistanceFadeStartSecond","label":"Distance Fade Start Second","kind":"SLIDER","size":1,"min":0.001,"max":3000,"default":[0.0010000000474974513]},{"name":"_DistanceFadeEndSecond","label":"Distance Fade End Second","kind":"SLIDER","size":1,"min":0.001,"max":3000,"default":[0.0010000000474974513]},{"name":"_AttenuationLerpLinearQuad","label":"Lerp between attenuation linear and quad","kind":"VALUE","size":1,"default":[0.5]},{"name":"_DepthBlendDistance","label":"Depth Blend Distance","kind":"VALUE","size":1,"default":[2]},{"name":"_DepthBlendCapOff","label":"Depth Blend Cap Off","kind":"SWITCH","size":1,"default":[0]},{"name":"_GlareFrontal","label":"Glare Frontal","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_GlareBehind","label":"Glare from Behind","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_UseClippingPlane","label":"Use Clipping Plane","kind":"SWITCH","size":1,"default":[0]},{"name":"_AdditionalClippingPlaneWS","label":"Additional Clipping Plane WS","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_ClippingPlaneTransition","label":"Clipping Plane Transition","kind":"SLIDER","size":1,"min":0.01,"max":10,"default":[1]},{"name":"_ShadowColor","label":"Shadow Color","kind":"COLOR","size":4,"default":[0,0,0,1],"gamma":true},{"name":"_CircleFade","label":"Circle Fade","kind":"SWITCH","size":1,"default":[0]},{"name":"_CircleFadeDistance","label":"Circle Fade Distance","kind":"VALUE","size":1,"default":[1]},{"name":"_CircleFadeSmoothness","label":"Circle Fade Smoothness","kind":"VALUE","size":1,"default":[0.20000000298023224]},{"name":"_DisableSceneShadow","label":"Disable Scene Shadow","kind":"SWITCH","size":1,"default":[0]},{"name":"_DisableCharacterSelfShadow","label":"Disable Character Self Shadow","kind":"SWITCH","size":1,"default":[0]},{"name":"_CapsuleAoColor","label":"Capsule AO Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true}]},{"name":"\u5F15\u64CE\u5168\u5C40 CP","gate":null,"rows":[{"name":"_CharacterParams0","label":"CP0 (.x=\u4E3B\u5149\u624B\u52A8\u63A7\u5236 .y=\u4E3B\u5149\u5F3A\u5EA6 .z=\u80CC\u5149\u9762\u5F3A\u5EA6 .w=\u73AF\u5883\u4EAE\u5EA6)","kind":"VECTOR","size":4,"default":[1,0.8999999761581421,0.800000011920929,0.800000011920929]},{"name":"_CharacterParams1","label":"CP1 (.x=\u6F14\u51FA\u6A21\u5F0F .y=\u9634\u5F71\u8272\u8C03\u6A21\u5F0F .z=\u5FFD\u7565\u4E3B\u5149\u9634\u5F71 .w=\u4E3B\u5149\u65B9\u5411\u975E\u573A\u666F)","kind":"VECTOR","size":4,"default":[0,1,1,0]},{"name":"_CharacterParams2","label":"CP2 (\u9634\u5F71\u8272\u503E\u5411\uFF0C\u76AE\u80A4\u4EE5\u5916)","kind":"VECTOR","size":4,"default":[0.8783068656921387,0.9302293062210083,1.1216931343078613,1]},{"name":"_CharacterParams3","label":"CP3 (\u9634\u5F71\u8272\u503E\u5411\uFF0C\u76AE\u80A4)","kind":"VECTOR","size":4,"default":[1.1870100498199463,0.9272286891937256,0.8129898905754089,1]},{"name":"_CharacterParams4","label":"CP4 (\u4E3B\u5149\u81EA\u5B9A\u4E49\u989C\u8272\uFF0C\u76AE\u80A4)","kind":"VECTOR","size":4,"default":[1,1,1,1]},{"name":"_CharacterParams5","label":"CP5 (\u4E3B\u5149\u81EA\u5B9A\u4E49\u989C\u8272)","kind":"VECTOR","size":4,"default":[1,1,1,1]},{"name":"_CharacterParams6","label":"CP6 (\u73AF\u5883\u5149\u65B9\u5411 = charGlobalAmbientParam0)","kind":"VECTOR","size":4,"default":[0,1,4.371138828673793E-08,0]},{"name":"_CharacterParams7","label":"CP7 (\u73AF\u5883\u5149\u7CFB\u6570 = charGlobalAmbientParam1)","kind":"VECTOR","size":4,"default":[0.15000000596046448,1.5,0.5,0]},{"name":"_CharacterParams8","label":"CP8 (.rgb=\u4E00\u952E\u8FB9\u7F18\u5149\u989C\u8272 .w=\u5F3A\u5EA6)","kind":"VECTOR","size":4,"default":[0,0,0,1]},{"name":"_CharacterParams9","label":"CP9 (.xy=\u4E00\u952E\u8FB9\u7F18\u5149\u65B9\u5411 .z=\u53D7\u57FA\u7840\u8272\u5F71\u54CD .w=\u5BBD\u5EA6)","kind":"VECTOR","size":4,"default":[8.742277657347586E-08,-1,0,0.4000000059604645]},{"name":"_CharacterParams10","label":"CP10 (\u89D2\u8272\u96E8\u6548\u9884\u89C8 .x=\u5F00\u5173 .y=\u5F3A\u5EA6 .z=\u8D34\u56FE\u5E73\u94FA .w=\u6C34\u7EBF\u9AD8\u5EA6)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_RuriCharacterEnvironmentEffect","label":"\u73AF\u5883\u6548\u679C\u91CF (.x=\u96E8 .y=\u6C34\u4F4D\u91CF .z=\u6D78\u6DA6 .w=\u96EA)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_RuriCharacterEnvironmentWater","label":"\u73AF\u5883\u6548\u679C\u6C34\u9762 (.x=\u4E16\u754C\u9AD8\u5EA6)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_CharacterParams11","label":"CP11 (.xyz=\u81EA\u5B9A\u4E49\u4E3B\u5149\u65B9\u5411 .w=\u660E\u6697\u4EA4\u754C\u7EBF\u504F\u79FB)","kind":"VECTOR","size":4,"default":[-0.4330127537250519,0.5,0.75,-0.4000000059604645]},{"name":"_CharacterParams12","label":"CP12 (.x=\u4EA4\u754C\u7EBF\u504F\u79FB\u5DF2\u8986\u5199 .y=\u4E3B\u5149\u989C\u8272\u5DF2\u8986\u5199 .z=\u6536\u573A\u666F\u9644\u52A0\u5149 .w=\u5FFD\u7565\u573A\u666F\u73AF\u5883)","kind":"VECTOR","size":4,"default":[1,0,1,0]},{"name":"_CharacterParams13","label":"CP13 (.x=\u773C\u775B\u6574\u4F53\u4EAE\u5EA6 .y=\u773C\u775B\u9AD8\u5149 .z=\u773C\u775B\u6563\u5C04 .w=\u4E3B\u5149\u9AD8\u5149\u7CFB\u6570)","kind":"VECTOR","size":4,"default":[0,0,0,1]},{"name":"_CharacterParams14","label":"CP14 (.rgb=\u8138\u90E8\u8FB9\u7F18\u5149\u989C\u8272 .w=\u5F3A\u5EA6)","kind":"VECTOR","size":4,"default":[0,0,0,1]},{"name":"_CharacterParams15","label":"CP15 (.xyz=\u8138\u90E8\u8FB9\u7F18\u5149\u65B9\u5411 .w=\u8FB9\u7F18\u5149\u65B0\u7B97\u6CD5)","kind":"VECTOR","size":4,"default":[0,0.0010000000474974513,-1,0]},{"name":"_RuriCharacterLightFollow","label":"\u4E3B\u5149\u8DDF\u968F\u76F8\u673A (.x=\u5F00 .y=\u4FEF\u4EF0\u4E0B\u9650 .z=\u504F\u822A\u504F\u7F6E)","kind":"VECTOR","size":4,"default":[0,32,10,0]},{"name":"_EnvironmentGlobalParams0","label":"EnvGlobalParams0 (.x=\u73AF\u5883\u5149 .y=\u53CD\u5C04)","kind":"VECTOR","size":4,"default":[1,1,1,0]}]},{"name":"PBR \u57FA\u7840","gate":null,"rows":[{"name":"_MetallicGlossMap","label":"RGBA:Metal,Spec,Shadow,Smooth","kind":"TEXTURE"},{"name":"_UseMetallicGlossMap","label":"Use MetallicGlossMap","kind":"SWITCH","size":1,"default":[0]},{"name":"_Metallic","label":"Metallic","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0],"gamma":true},{"name":"_Specular","label":"Specular Scale","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_Smoothness","label":"Smoothness","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]}]},{"name":"\u81EA\u53D1\u5149","gate":null,"rows":[{"name":"_EmissionBrightness","label":"Emission Brightness","kind":"VALUE","size":1,"default":[1]}]},{"name":"Ramp","gate":null,"rows":[{"name":"_DiffRampMap","label":"Diffuse Ramp","kind":"TEXTURE"},{"name":"_SpecRampMap","label":"Specular Ramp","kind":"TEXTURE"},{"name":"_UseDiffRampMap","label":"Diffuse Ramp","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseSpecRampMap","label":"Specular Ramp","kind":"SWITCH","size":1,"default":[0]},{"name":"_SpecRampIridescentMode","label":"\u5F69\u8679\u8272\u6A21\u5F0F(\u956D\u5C04\u5851\u6599\u8BF7\u52FE\u9009)","kind":"SWITCH","size":1,"default":[0]}]},{"name":"\u9634\u5F71\u8272","gate":null,"rows":[{"name":"_ShadowLutTex","label":"Shadow Color Lut","kind":"TEXTURE"},{"name":"_UseShadowLutTex","label":"Use Shadow Color LUT Tex","kind":"SWITCH","size":1,"default":[0]},{"name":"_ShadowColorBrightness","label":"Shadow Color Brightness","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_ShadowColorSaturation","label":"Shadow Color Saturation","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]}]},{"name":"\u8138\u90E8 SDF/\u8868\u60C5","gate":null,"rows":[{"name":"_SDFMask","label":"RimMask/SDFMask/FlatSHMask","kind":"TEXTURE"},{"name":"_SDFLightmap","label":"SDF Lightmap","kind":"TEXTURE"},{"name":"_EmotionMap","label":"Emotion Map","kind":"TEXTURE"},{"name":"_HighlightMap","label":"HighlightMap","kind":"TEXTURE"},{"name":"_UseSDFLightmap","label":"Use SDF Lightmap","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseEmotionMap","label":"Use Emotion Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_EmotionIndex","label":"Emotion Index","kind":"SLIDER","size":1,"min":0,"max":3,"default":[0]},{"name":"_EmotionBlend","label":"Emotion Blend","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_SDFRimColor","label":"Skin Rim Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_SkinRimOffScale","label":"Skin Rim Scale","kind":"SLIDER","size":1,"min":0,"max":1.5,"default":[0.5]},{"name":"_FaceRimOffScale","label":"Face Rim Scale (SDF Area)","kind":"SLIDER","size":1,"min":0,"max":1.5,"default":[1]},{"name":"_FaceHighlightMap","label":"Use Face Highlight Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_HighlightMapVector","label":"HighlightMap Vector","kind":"VECTOR","size":4,"default":[0.03999999910593033,-0.009999999776482582,0,0]}]},{"name":"\u8138\u90E8\u8D34\u82B1","gate":null,"rows":[{"name":"_FaceDecalTintColor","label":"Face Decal Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_FaceDecalCenterX","label":"Face Decal Center X","kind":"SLIDER","size":1,"min":-0.5,"max":0.5,"default":[0]},{"name":"_FaceDecalCenterY","label":"Face Decal Center Y","kind":"SLIDER","size":1,"min":-0.5,"max":0.5,"default":[0]},{"name":"_FaceDecalInvertX","label":"Face Decal Invert X","kind":"SWITCH","size":1,"default":[0]},{"name":"_FaceDecalInvertY","label":"Face Decal Invert Y","kind":"SWITCH","size":1,"default":[0]},{"name":"_FaceDecalSize","label":"Face Decal Size","kind":"SLIDER","size":1,"min":0.05,"max":2,"default":[0.20000000298023224]},{"name":"_FaceDecalRotation","label":"Face Decal Rotation","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_FaceDecalMirrorMode","label":"Face Decal Mirror Mode","kind":"VALUE","size":1,"default":[0]},{"name":"_FaceDecalMirrorSplit","label":"Face Decal Mirror Split","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_FaceDecalBrightnessMask","label":"Face Decal Brightness Mask","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.699999988079071]}]},{"name":"\u773C\u775B Matcap","gate":null,"rows":[{"name":"_MatcapTex","label":"Matcap","kind":"TEXTURE"},{"name":"_UseMatcap","label":"Use Matcap","kind":"SWITCH","size":1,"default":[0]},{"name":"_EyeHighLight","label":"Eye High Light","kind":"SWITCH","size":1,"default":[0]},{"name":"_MatcapNormalScale","label":"Matcap Normal Scale","kind":"SLIDER","size":1,"min":0,"max":1.5,"default":[1]},{"name":"_MatcapColor","label":"Matcap Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_EyeHighLightColor","label":"High Light Color","kind":"HDRCOLOR","size":4,"default":[2,2,2,1]},{"name":"_EyeScatteringColor","label":"Scattering Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]},{"name":"_EyeTintColor","label":"Eye Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true}]},{"name":"\u5934\u53D1\u9AD8\u5149/\u63CF\u7EBF","gate":null,"rows":[{"name":"_SplitNormalMap","label":"Hair Normal Map","kind":"TEXTURE"},{"name":"_StrokeMap","label":"Stroke Map(R:anisotropy G:specular offset)","kind":"TEXTURE"},{"name":"_LineMap","label":"Line Map","kind":"TEXTURE"},{"name":"_UseSpecBumpMap","label":"Split Diffuse / Specular Normal","kind":"SWITCH","size":1,"default":[0]},{"name":"_SpecBumpScale","label":"Spec Scale","kind":"VALUE","size":1,"default":[1]},{"name":"_StrokeOn","label":"Use Stroke Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_SpecularLine","label":"SpecularLine","kind":"SWITCH","size":1,"default":[0]},{"name":"_DrawUnderBrow","label":"Draw Under Brow","kind":"SWITCH","size":1,"default":[0]},{"name":"_AnisotropyValue","label":"Anisotropy Value","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.3499999940395355]},{"name":"_AnisotropyValue2","label":"Anisotropy Value2","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.4000000059604645]},{"name":"_AnisotropyDirX","label":"Anisotropy Direction X","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_AnisotropyIntensity","label":"Anisotropy Intensity","kind":"SLIDER","size":1,"min":0,"max":3,"default":[1]},{"name":"_AnisotropyEdgeFade","label":"Anisotropy Edge Fade","kind":"SLIDER","size":1,"min":0.01,"max":10,"default":[1]},{"name":"_AnisotropyRange2","label":"Anisotropy Range2","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_AnisotropyColor2","label":"Anisotropy Color2","kind":"COLOR","size":4,"default":[0,0,0,1],"gamma":true},{"name":"_StrokeScale","label":"Stroke Scale","kind":"VALUE","size":1,"default":[1]},{"name":"_UseLineMap","label":"Use Line Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_LineAmount","label":"Line Amount","kind":"VALUE","size":1,"default":[300]},{"name":"_LineValue","label":"Line Value","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_LineRange","label":"Line Range","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_LineIntensity","label":"Line Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_LineSaturation","label":"Line Saturation","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_HairBaseTintColor","label":"Hair Base Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_HairAddTintColor","label":"Hair Add Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true}]},{"name":"\u76AE\u6BDB","gate":"_UseCharacterFur","rows":[{"name":"_UseCharacterFur","label":"Use CharacterFur","kind":"SWITCH","size":1,"default":[0]},{"name":"_FurMap","label":"Fur Noise","kind":"TEXTURE"},{"name":"_FurDirMap","label":"\u6BDB\u53D1\u65B9\u5411(RG)\u758F\u5BC6(B)\u957F\u77ED(A)","kind":"TEXTURE"},{"name":"_FurDyeMap","label":"\u76AE\u6BDB\u67D3\u8272","kind":"TEXTURE"},{"name":"_FurDyeEnable","label":"\u4F7F\u7528\u76AE\u6BDB\u67D3\u8272\u529F\u80FD","kind":"SWITCH","size":1,"default":[0]},{"name":"_FurDyeIntensity","label":"\u67D3\u8272\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_FurLengthIntensity","label":"\u6BDB\u53D1\u957F\u5EA6","kind":"SLIDER","size":1,"min":0.001,"max":6,"default":[1]},{"name":"_FurCutoffStart","label":"\u53D1\u6839CutOff","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_FurCutoffEnd","label":"\u53D1\u5C3ECutOff","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_FurAO","label":"\u53D1\u6839AO","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_FurEdgeFade","label":"\u8FB9\u7F18\u5E73\u6ED1\u8FC7\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_FurGravityStrength","label":"\u91CD\u529B\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_FurTTIntensity","label":"\u76F4\u5C04\u5149\u900F\u5149\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_FurDirMapEnable","label":"\u4F7F\u7528\u6BDB\u53D1\u65B9\u5411\u8D34\u56FE(RG)","kind":"SWITCH","size":1,"default":[0]},{"name":"_FurFlowBaseStrength","label":"\u6BDB\u53D1\u65B9\u5411\u504F\u79FB\u5F3A\u5EA6","kind":"SLIDER","size":1,"min":0,"max":0.06,"default":[0.004999999888241291]},{"name":"_FurColorEnable","label":"\u4F7F\u7528\u5C16\u7AEF\u8C03\u8272","kind":"SWITCH","size":1,"default":[0]},{"name":"_FurColor","label":"\u5C16\u7AEF\u8C03\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_FurSharpen","label":"\u76AE\u6BDB\u5C16\u9510","kind":"SWITCH","size":1,"default":[0]},{"name":"_FurNoise","label":"\u76AE\u6BDB\u53E0\u52A0\u566A\u58F0","kind":"SWITCH","size":1,"default":[0]}]},{"name":"\u6E05\u6F06","gate":"_ClearCoat","rows":[{"name":"_ClearCoat","label":"ClearCoat Effect","kind":"SWITCH","size":1,"default":[0]},{"name":"_ClearCoatMask","label":"ClearCoat Mask","kind":"TEXTURE"},{"name":"_ClearCoatColor","label":"ClearCoat Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ClearCoatSmoothness","label":"ClearCoat Smoothness","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.949999988079071]},{"name":"_ClearCoatMetallic","label":"ClearCoat Metallic","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_ClearCoatNormalMode","label":"ClearCoat Normal","kind":"VALUE","size":1,"default":[0]}]},{"name":"\u89C6\u5DEE","gate":"_UseParallax","rows":[{"name":"_UseParallax","label":"Use Parallax","kind":"SWITCH","size":1,"default":[0]},{"name":"_ParallaxTex","label":"Parallax Tex","kind":"TEXTURE"},{"name":"_ParallaxUseNormal","label":"Parallax Use Normal Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_ParallaxMarchNum","label":"Parallax March Num","kind":"SLIDER","size":1,"min":1,"max":5,"default":[3]},{"name":"_ParallaxScale","label":"Parallax Scale","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_ParallaxColor","label":"Parallax Color","kind":"HDRCOLOR","size":4,"default":[0,0,0,1],"gamma":true}]},{"name":"\u4E1D\u889C","gate":"_SilkStockings","rows":[{"name":"_SilkStockings","label":"Silk Stockings","kind":"SWITCH","size":1,"default":[0]},{"name":"_SilkStockingsMask","label":"\u4E1D\u889C\u906E\u7F69","kind":"TEXTURE"},{"name":"_SilkStockingsColor","label":"\u4E1D\u889C\u8FB9\u7F18\u989C\u8272","kind":"COLOR","size":4,"default":[0,0,0,1],"gamma":true},{"name":"_SilkStockingsSpecularInt","label":"\u4E1D\u889C\u9AD8\u5149\u5F3A\u5EA6Remap","kind":"VALUE","size":1,"default":[5]},{"name":"_SilkStockingsSpecularValue","label":"\u4E1D\u889C\u9AD8\u5149\u4F4D\u7F6E\u504F\u79FB","kind":"SLIDER","size":1,"min":-2,"max":2,"default":[2]},{"name":"_SilkStockingsAnisoDirection","label":"\u4E1D\u889C\u9510\u5229\u5EA6G","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_SilkStockingsDryColor","label":"\u4E1D\u889C\u5E38\u6001\u504F\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_SilkStockingsWetColor","label":"\u4E1D\u889C\u6E7F\u6DA6\u504F\u8272","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_SilkStockingsMinAffect","label":"\u4E1D\u889C\u6700\u6D45\u8986\u76D6","kind":"SLIDER","size":1,"min":0,"max":0.49,"default":[0.05000000074505806]},{"name":"_SilkStockingsMaxAffect","label":"\u4E1D\u889C\u6700\u6DF1\u8986\u76D6","kind":"SLIDER","size":1,"min":0.5,"max":0.9,"default":[0.8999999761581421]},{"name":"_SilkStockingsAdvance","label":"\u4E1D\u889C\u9AD8\u7EA7\u6A21\u5F0F(\u4F7F\u7528\u8D34\u56FE)","kind":"SWITCH","size":1,"default":[0]},{"name":"_SilkStockingsSpecularMinAtMinWetness","label":"\u4E1D\u889C\u9AD8\u5149\u5E72\u71E5\u6001\u6700\u5C0F\u503C","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_SilkStockingsSpecularFalloff","label":"\u4E1D\u889C\u9AD8\u5149\u900F\u8089\u8870\u51CF\u503C","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.800000011920929]},{"name":"_SilkStockingsRainWetMaskScale","label":"\u4E1D\u889C\u6D78\u6DA6\u5185\u7F6E\u906E\u7F69\u5F71\u54CD","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.699999988079071]},{"name":"_SilkStockingsAlbedoAffectType","label":"\u6D78\u6DA6\u6216\u6C34\u4E0B\u65F6\u900F\u8089or\u538B\u6697","kind":"SLIDER","size":1,"min":-0.9,"max":0.5,"default":[0.5]}]},{"name":"\u5404\u5411\u5F02\u6027","gate":"_UseAnisotropy","rows":[{"name":"_UseAnisotropy","label":"Use Anisotropy","kind":"SWITCH","size":1,"default":[0]},{"name":"_AnisotropyUseGeometryTangent","label":"\u4F7F\u7528\u6A21\u578B\u5207\u7EBF","kind":"SWITCH","size":1,"default":[1]},{"name":"_AnisotropyDirectionMain","label":"\u57FA\u7840\u5404\u9879\u5F02\u6027\u9AD8\u5149\u65B9\u5411","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_AnisotropyIntensityMultiplier","label":"\u57FA\u7840\u5404\u9879\u5F02\u6027\u9AD8\u5149\u5F3A\u5EA6\u7CFB\u6570","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_AnisotropyDirectionAdditional","label":"\u7B2C\u4E8C\u5C42\u5404\u5411\u5F02\u6027\u65B9\u5411","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_AnisotropyOffsetAdditional","label":"\u7B2C\u4E8C\u5C42\u5404\u5411\u5F02\u6027\u4F4D\u7F6E\u504F\u79FB","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_AnisotropyColorAdditional","label":"\u7B2C\u4E8C\u5C42\u5404\u5411\u5F02\u6027\u989C\u8272","kind":"COLOR","size":4,"default":[0.20000000298023224,0.20000000298023224,0.20000000298023224,1],"gamma":true}]},{"name":"UV2 \u67D3\u8272","gate":null,"rows":[{"name":"_UseUV2Color","label":"UV2 Color","kind":"SWITCH","size":1,"default":[0]},{"name":"_ExtraRootTintColor","label":"Root Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ExtraDepthTintColor","label":"Depth Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_ViewFade","label":"View Fade","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0]}]},{"name":"\u634F\u4EBA\u67D3\u8272","gate":"_AvatarCustomizeEnable","rows":[{"name":"_AvatarCustomizeEnable","label":"Avatar System Input","kind":"SWITCH","size":1,"default":[0]},{"name":"_CustomizeBaseColor","label":"Customize Base Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_CustomizeBaseTintColor","label":"Customize Base Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_CustomizeAddTintColor","label":"Customize Add Tint Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true}]},{"name":"\u6DF1\u5EA6\u6DE1\u51FA","gate":"_UseDepthFade","rows":[{"name":"_UseDepthFade","label":"Use Depth Fade","kind":"SWITCH","size":1,"default":[0]},{"name":"_DepthFadeValue","label":"Depth Fade Value","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_DepthFadeExp","label":"Depth Fade Exp","kind":"SLIDER","size":1,"min":0.001,"max":50,"default":[10]}]},{"name":"\u4FB5\u8680","gate":"_UseCharacterErosion","rows":[{"name":"_UseCharacterErosion","label":"Use Character Erosion","kind":"SWITCH","size":1,"default":[0]},{"name":"_ErosionMetallic","label":"Erosion Metallic","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_ErosionSmoothnessBias","label":"Erosion Smoothness Bias","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_ErosionNormalScale","label":"Erosion Normal Scale","kind":"SLIDER","size":1,"min":0,"max":4,"default":[1]},{"name":"_ErosionBaseColor","label":"Erosion Base Color","kind":"COLOR","size":4,"default":[0.800000011920929,0.4000000059604645,0.5,1],"gamma":true},{"name":"_ErosionUV2Tint","label":"Erosion Tint UV2 Enable","kind":"SWITCH","size":1,"default":[0]},{"name":"_ErosionBaseRootColor","label":"Erosion Base Root Color","kind":"COLOR","size":4,"default":[0.10000000149011612,0.10000000149011612,0.10000000149011612,1],"gamma":true},{"name":"_ErosionBaseRootColorLocation","label":"Erosion Root Color Location","kind":"SLIDER","size":1,"min":0,"max":0.9,"default":[0.10000000149011612]},{"name":"_ErosionBaseRootColorSmooth","label":"Erosion Root Color Smooth","kind":"SLIDER","size":1,"min":0,"max":0.25,"default":[0.10000000149011612]},{"name":"_ErosionBaseTopColor","label":"Erosion Top Color","kind":"COLOR","size":4,"default":[0.75,0.75,0.75,1],"gamma":true},{"name":"_ErosionBaseTopColorLocation","label":"Erosion Top Color Location","kind":"SLIDER","size":1,"min":0,"max":0.9,"default":[0.699999988079071]},{"name":"_ErosionBaseTopColorSmooth","label":"Erosion Top Color Smooth","kind":"SLIDER","size":1,"min":0,"max":0.25,"default":[0.10000000149011612]},{"name":"_ErosionPatternTintColor","label":"Erosion Pattern Tint Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1]}]},{"name":"\u5080\u5121","gate":"_UsePuppet","rows":[{"name":"_UsePuppet","label":"Use Puppet Effect","kind":"SWITCH","size":1,"default":[0]},{"name":"_PuppetUV2AreaMask","label":"Puppet UV2 Area Mask","kind":"SWITCH","size":1,"default":[0]},{"name":"_PuppetMaskLocationDown","label":"Puppet Mask Location Down","kind":"SLIDER","size":1,"min":0,"max":0.9,"default":[0.10000000149011612]},{"name":"_PuppetMaskLocationTop","label":"Puppet Mask Location Top","kind":"SLIDER","size":1,"min":0,"max":0.9,"default":[0.5]},{"name":"_PuppetMaskSmooth","label":"Puppet Mask Smooth","kind":"SLIDER","size":1,"min":0.01,"max":0.25,"default":[0.10000000149011612]},{"name":"_PuppetProceduralDCurveEnable","label":"Puppet Procedural DCurve Color","kind":"SWITCH","size":1,"default":[0]},{"name":"_PuppetPDCurveUVScaleSpeed","label":"Puppet DCurve UV2 Scale(XY) Speed(ZW)","kind":"VECTOR","size":4,"default":[120,12,0,-0.05999999865889549]},{"name":"_PuppetPDCurveDistortSpeed","label":"Puppet DCurve Distort Speed","kind":"VALUE","size":1,"default":[0.5]},{"name":"_PuppetPDCurveDistortPeriodSpeed","label":"Puppet DCurve Period Speed","kind":"VALUE","size":1,"default":[0.5]},{"name":"_PuppetPDCurveBaseColor","label":"Puppet DCurve Base Color","kind":"COLOR","size":4,"default":[0.38999998569488525,0.5799999833106995,0.699999988079071,1],"gamma":true},{"name":"_PuppetPDCurveLightColor","label":"Puppet DCurve Light Color","kind":"COLOR","size":4,"default":[0.5699999928474426,0.30000001192092896,0.8299999833106995,0.5],"gamma":true},{"name":"_PuppetPDCurveEdgeColor","label":"Puppet DCurve Edge Color","kind":"COLOR","size":4,"default":[0.44999998807907104,0.3799999952316284,0.7300000190734863,0.5],"gamma":true},{"name":"_PuppetPDCurveEdgeLocation","label":"Puppet DCurve Edge Location(1 = Unuse)","kind":"SLIDER","size":1,"min":0.01,"max":1,"default":[0.30000001192092896]},{"name":"_PuppetPatternSpeed","label":"Puppet Pattern Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_PuppetPatternMapUseRGB","label":"Puppet Pattern Map Use RGB","kind":"VALUE","size":1,"default":[0]},{"name":"_PuppetBaseColor","label":"Puppet Base Color","kind":"COLOR","size":4,"default":[0.5,0.6499999761581421,0.800000011920929,1],"gamma":true},{"name":"_PuppetPatternTintColor","label":"Puppet Pattern Tint Color","kind":"HDRCOLOR","size":4,"default":[0.4000000059604645,0.20000000298023224,0.9399999976158142,1]},{"name":"_PuppetPatternTintEdgeColor","label":"Puppet Pattern Tint Edge Color","kind":"COLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_PuppetPatternTintEdgeLocation","label":"Puppet Pattern Tint Edge Location(1 = Unuse)","kind":"SLIDER","size":1,"min":0.01,"max":1,"default":[1]},{"name":"_PuppetMetallic","label":"Puppet Metallic","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_PuppetRoughness","label":"Puppet Roughness","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]}]},{"name":"\u98CE\u683C\u5316\u83F2\u6D85\u5C14","gate":"_EnableStylizedFresnel","rows":[{"name":"_EnableStylizedFresnel","label":"Stylized Fresnel","kind":"SWITCH","size":1,"default":[0]},{"name":"_StylizedFresnelColor","label":"Color(A = Emission)","kind":"COLOR","size":4,"default":[0,0,0,0],"gamma":true},{"name":"_StylizedFresnelPow","label":"Pow","kind":"SLIDER","size":1,"min":0,"max":10,"default":[2]},{"name":"_StylizedFresnelAmount","label":"Amount","kind":"VALUE","size":1,"default":[2]},{"name":"_StylizedFresnelNoiseSpeed","label":"Noise Speed","kind":"VALUE","size":1,"default":[0]},{"name":"_StylizedNoiseContrast","label":"Noise Contrast","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]}]},{"name":"\u53D7\u51FB\u95EA\u5149","gate":"_EnableEnemyHitFlash","rows":[{"name":"_EnableEnemyHitFlash","label":"Enemy Hit Flash","kind":"SWITCH","size":1,"default":[0]},{"name":"_EnemyHitFlashBrightColor","label":"Bright(Scanline) Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_EnemyHitFlashInnerRadius","label":"\u7FBD\u5316\u5185\u534A\u5F84","kind":"SLIDER","size":1,"min":0,"max":10,"default":[0]},{"name":"_EnemyHitFlashOuterRadius","label":"\u7FBD\u5316\u5916\u534A\u5F84","kind":"SLIDER","size":1,"min":0,"max":10,"default":[2]},{"name":"_EnemyHitFlashBrightCenter","label":"\u8986\u76D6\u4E2D\u5FC3\u5750\u6807,0\u4E3A\u9ED8\u8BA4\u4E3B\u89D2\u4F4D\u7F6E","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_EnemyHitFlashFresnelColor","label":"Fresnel Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_EnemyHitFlashFresnelBias","label":"Fresnel Bias(Default:0)","kind":"SLIDER","size":1,"min":-1,"max":2,"default":[0]},{"name":"_EnemyHitFlashFresnelAffectOpacity","label":"Fresnel Affect Opacity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_EnemyHitFlashNormalScale","label":"Normal Scale","kind":"SLIDER","size":1,"min":0,"max":3,"default":[1]},{"name":"_EnemyHitFlashBrightColorAdjust","label":"Bright Color Adjust","kind":"VALUE","size":1,"default":[1]},{"name":"_EnemyHitFlashFresnelColorAdjust","label":"Fresnel Color Adjust","kind":"VALUE","size":1,"default":[1]}]},{"name":"\u7403\u5F62\u6296\u52A8\u6D88\u9690","gate":"_EnableDitherSphere","rows":[{"name":"_EnableDitherSphere","label":"Enable Sphere Dither","kind":"SWITCH","size":1,"default":[0]},{"name":"_DitherSphereRadius","label":"Dither Sphere Radius","kind":"SLIDER","size":1,"min":0,"max":0.3,"default":[0.05000000074505806]},{"name":"_DitherSphereSmoothness","label":"Dither Sphere Smoothness","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0.20000000298023224]}]},{"name":"VAT \u52A8\u753B","gate":"_UseVATMap","rows":[{"name":"_UseVATMap","label":"UseVATMap","kind":"SWITCH","size":1,"default":[0]},{"name":"_DebugVATFrameIndex","label":"DebugVATFrame","kind":"SWITCH","size":1,"default":[0]},{"name":"_VATFrameIndex","label":"VAT Frame Index","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]}]},{"name":"UV \u6D41\u52A8/\u547C\u5438","gate":null,"rows":[{"name":"_BaseMapUVSpeed","label":"BaseMap UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_EmissionMapUVSpeed","label":"EmissionMap UV Speed","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_EmissionAlphaBrightBreath","label":"Emission\u547C\u5438\uFF08A\uFF09","kind":"SWITCH","size":1,"default":[0]},{"name":"_EmissionAlphaBrightBreathSpeed","label":"Emission\u547C\u5438\u901F\u5EA6","kind":"VALUE","size":1,"default":[1]},{"name":"_EmissionAlphaBrightBreathScaleMin","label":"Emission\u547C\u5438\u6700\u5C0F\u4EAE\u5EA6","kind":"VALUE","size":1,"default":[0.5]},{"name":"_EmissionAlphaBrightBreathScaleMax","label":"Emission\u547C\u5438\u6700\u5927\u4EAE\u5EA6","kind":"VALUE","size":1,"default":[1]}]},{"name":"\u9876\u70B9\u52A8\u753B","gate":"_VertexAnimationEnable","rows":[{"name":"_VertexAnimationEnable","label":"Vertex Animation","kind":"SWITCH","size":1,"default":[0]},{"name":"_VertexAnimationIntensity","label":"Vertex Animation Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.10000000149011612]},{"name":"_VertexAnimationFrequency","label":"Vertex Animation Frequency","kind":"SLIDER","size":1,"min":0,"max":30,"default":[0.5]},{"name":"_VertexAnimationWaveLength","label":"Vertex Animation WaveLength","kind":"SLIDER","size":1,"min":0,"max":20,"default":[0]},{"name":"_VertexAnimationFalloff","label":"Vertex Animation Falloff","kind":"SLIDER","size":1,"min":0.1,"max":10,"default":[1]},{"name":"_VertexAnimationExpandOnly","label":"Vertex Animation Expand Only","kind":"SWITCH","size":1,"default":[0]},{"name":"_VertexAnimationDirection","label":"Vertex Animation Direction","kind":"VECTOR","size":4,"default":[1,0,1,0]},{"name":"_VertexAnimationNoiseIntensity","label":"Vertex Animation Noise Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_VertexAnimationNoiseTiling","label":"Vertex Animation Noise Tiling","kind":"SLIDER","size":1,"min":0.5,"max":4,"default":[1]},{"name":"_VertexAnimationNoiseFrequency","label":"Vertex Animation Noise Frequency","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.20000000298023224]}]},{"name":"\u81EA\u9634\u5F71","gate":null,"rows":[{"name":"_DisableSelfShadow","label":"Disable Self Shadow","kind":"SWITCH","size":1,"default":[0]}]},{"name":"\u89D2\u8272 VFX","gate":null,"rows":[{"name":"_EnableCharacterVFX","label":"Character VFX","kind":"SWITCH","size":1,"default":[0]}]},{"name":"VFX \u5408\u6210","gate":null,"rows":[{"name":"_VFXSpecialMainTex","label":"VFX Special Main Tex","kind":"TEXTURE"},{"name":"_VFXSpecialBlendTex","label":"VFX Special Blend Tex","kind":"TEXTURE"},{"name":"_UseMask","label":"Use Mask (\u53EA\u5F71\u54CDAlpha)","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseDisturb","label":"Use Disturb","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseFresnel","label":"Use Fresnel","kind":"SWITCH","size":1,"default":[0]},{"name":"_VFXColor","label":"VFX Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_VFXColorIntensity","label":"VFX Color Intensity (Default 1)","kind":"SLIDER","size":1,"min":1,"max":100,"default":[1]},{"name":"_VFXColorAlpha","label":"VFX Color Alpha (Default 1)","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_UseVFXMainTexAsAlpha","label":"UseMainTexAsAlpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_VFXSpecialBlendTexRForDisturb","label":"Use Blend Tex R For Disturb","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_VFXBlendTint","label":"BlendTint","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_VFXSpecialParam","label":"VFX Special Param(XY: MainTex, ZW: BlendTex)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_VFXFresnelColor","label":"Fresnel Color","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_VFXFresnelBias","label":"Fresnel Bias(Default:0)","kind":"SLIDER","size":1,"min":-1,"max":2,"default":[0]},{"name":"_VFXFresnelAffectOpacity","label":"Fresnel Affect Opacity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_VFXFresnelPower","label":"Fresnel Power(Default:1)","kind":"SLIDER","size":1,"min":1,"max":100,"default":[1]},{"name":"_VFXFresnelFlip","label":"Fresnel Flip","kind":"SWITCH","size":1,"default":[0.0010000000474974513]},{"name":"_SpecialDissolveScheduleOffset","label":"Dissolve Schedule Offset","kind":"SLIDER","size":1,"min":0,"max":2,"default":[0]}]},{"name":"\u7279\u6548\u8D34\u56FE/\u6D41\u52A8","gate":null,"rows":[{"name":"_MaskTex","label":"Mask Tex","kind":"TEXTURE"},{"name":"_DisturbTex1","label":"Disturb Tex 1","kind":"TEXTURE"},{"name":"_BlendMode","label":"Blend Type","kind":"VALUE","size":1,"default":[5]},{"name":"_DisableVertColor","label":"Disable VertColor","kind":"SWITCH","size":1,"default":[0]},{"name":"_InParticle","label":"Use In Particle","kind":"SWITCH","size":1,"default":[1]},{"name":"_VertCameraOffset","label":"\u9876\u70B9\u5411\u76F8\u673A\u504F\u79FB(\u5355\u4F4D\u7C73)","kind":"VALUE","size":1,"default":[0]},{"name":"_TintColor","label":"TintColor","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_TintColorIntensity","label":"Tint Color Intensity (Default 1)","kind":"SLIDER","size":1,"min":1,"max":100,"default":[1]},{"name":"_TintColorAlpha","label":"Tint Color Alpha (Default 1)","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_UseMainTexAsAlpha","label":"UseMainTexAsAlpha","kind":"SWITCH","size":1,"default":[1]},{"name":"_MainTexUseDisturb","label":"Main Tex Use Disturb","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_MainTexUVSpeed","label":"MainTexUVSpeed(XY:By Time,ZW:By Custom1.X)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_MainTexUVRotateMat","label":"MainTexUVRotateMat","kind":"VECTOR","size":4,"default":[1,0,0,1]},{"name":"_MainTexUVWeights","label":"\u0027_MainTexUVWeights\u0027","kind":"VECTOR","size":4,"default":[1,0,0,0]},{"name":"_UseMaskTexAsAlpha","label":"UseMaskTexAsAlpha","kind":"SWITCH","size":1,"default":[1]},{"name":"_MaskTexUseDisturb","label":"Mask Tex Use Disturb","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_MaskTexUVSpeed","label":"MaskTaexUVSpeed(XY:By Time,ZW:By Custom1.Y)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_MaskTexUVRotateMat","label":"MaskTexUVRotateMat","kind":"VECTOR","size":4,"default":[1,0,0,1]},{"name":"_MaskTexUVWeights","label":"\u0027_MaskTexUVWeights\u0027","kind":"VECTOR","size":4,"default":[1,0,0,0]},{"name":"_BlendTint","label":"BlendTint","kind":"HDRCOLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_Bi_Disturb","label":"Disturbe in 2 Direction","kind":"SWITCH","size":1,"default":[0]},{"name":"_DisturbTex1Normal","label":"Disturb Tex1 is Normal","kind":"SWITCH","size":1,"default":[0]},{"name":"_DisturbUIntensity1","label":"UIntensity1","kind":"VALUE","size":1,"default":[0]},{"name":"_DisturbVIntensity1","label":"VIntensity1(Unused In Normal)","kind":"VALUE","size":1,"default":[0]},{"name":"_DisturbUVSpeed1","label":"DisturbUVSpeed(XY:By Time,ZW:By Custom1.Y)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_DisturbUVRotateMat1","label":"DisturbUVRotateMat","kind":"VECTOR","size":4,"default":[1,0,0,1]},{"name":"_DisturbUVWeights1","label":"\u0027_DisturbTexUVWeights\u0027","kind":"VECTOR","size":4,"default":[1,0,0,0]},{"name":"_EnableNormalMap","label":"Normal Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_NormalMapUVSpeed","label":"NormalMapUVSpeed(XY:By Time,ZW:By Custom1.Y)","kind":"VECTOR","size":4,"default":[0,0,0,0]},{"name":"_NormalMapUVRotateMat","label":"NormalMapUVRotateMat","kind":"VECTOR","size":4,"default":[1,0,0,1]},{"name":"_NormalMapUVWeights","label":"\u0027_NormalMapUVWeights\u0027","kind":"VECTOR","size":4,"default":[1,0,0,0]}]},{"name":"\u7279\u6548\u83F2\u6D85\u5C14/\u8FD1\u6DE1\u51FA","gate":null,"rows":[{"name":"_FresnelBias","label":"Fresnel Bias(Default:0)","kind":"SLIDER","size":1,"min":-1,"max":2,"default":[0]},{"name":"_FresnelAffectOpacity","label":"Fresnel Affect Opacity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_FresnelPower","label":"Fresnel Power(Default:1)","kind":"SLIDER","size":1,"min":1,"max":10,"default":[1]},{"name":"_FresnelFlip","label":"Fresnel Flip","kind":"SWITCH","size":1,"default":[0.0010000000474974513]},{"name":"_UseNearCameraFade","label":"Use Near Camera Fade","kind":"SWITCH","size":1,"default":[0]},{"name":"_NearCameraFadeDistanceStart","label":"\u6D88\u5931\u8DDD\u79BB1","kind":"SLIDER","size":1,"min":0.001,"max":3000,"default":[0.0010000000474974513]},{"name":"_NearCameraFadeDistanceEnd","label":"\u51FA\u73B0\u8DDD\u79BB1","kind":"SLIDER","size":1,"min":0.001,"max":3000,"default":[10]},{"name":"_NearCameraFadeDistanceEnd2","label":"\u51FA\u73B0\u8DDD\u79BB2","kind":"SLIDER","size":1,"min":0.002,"max":3000,"default":[100]},{"name":"_NearCameraFadeDistanceStart2","label":"\u6D88\u5931\u8DDD\u79BB2","kind":"SLIDER","size":1,"min":0.001,"max":3000,"default":[120]}]},{"name":"\u7279\u6548\u6742\u9879","gate":null,"rows":[{"name":"_UseGrayAsAlpha","label":"Use Gray As Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_ShadowAngleRange","label":"Shadow Angle Range","kind":"SLIDER","size":1,"min":-0.01,"max":0.01,"default":[0]}]},{"name":"\u7279\u6548\u8C03\u8272","gate":"_EnableVFXColorAdjustment","rows":[{"name":"_EnableVFXColorAdjustment","label":"VFX Color Adjustment","kind":"SWITCH","size":1,"default":[0]},{"name":"_ColorAdjustmentContrast","label":"Color Adjustment Contrast","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_ColorAdjustmentSaturation","label":"Color Adjustment Saturation","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_ColorAdjustmentBrightness","label":"Color Adjustment Brightness","kind":"SLIDER","size":1,"min":0.5,"max":1.5,"default":[1]},{"name":"_ColorAdjustmentRimWidth","label":"Color Adjustment Rim Width","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.3499999940395355]},{"name":"_ColorAdjustmentRimIntensity","label":"Color Adjustment Rim Intensity","kind":"SLIDER","size":1,"min":0,"max":10,"default":[4]},{"name":"_ColorAdjustmentColorBlend","label":"Color Adjustment Color Blend","kind":"COLOR","size":4,"default":[1,1,1,0],"gamma":true},{"name":"_ColorAdjustmentRimColor","label":"Color Adjustment Rim Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true}]},{"name":"\u63CF\u8FB9","gate":null,"rows":[{"name":"_OutlineColorBrightness","label":"Outline Color Brightness","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.5]},{"name":"_OutlineColorSaturation","label":"Outline Color Saturation","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1.5]}]},{"name":"State","gate":null,"rows":[{"name":"_DoubleSided","label":"Double Sided","kind":"SWITCH","size":1,"default":[0]},{"name":"_Cull","label":"Cull","kind":"VALUE","size":1,"default":[2]},{"name":"_Cutoff","label":"Alpha Cutoff","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.15000000596046448]},{"name":"_PreMulAlpha","label":"Pre Mul Alpha","kind":"SWITCH","size":1,"default":[0]},{"name":"_OutlineOffset","label":"Outline Offset","kind":"SLIDER","size":1,"min":0,"max":20,"default":[0]},{"name":"_OutlineColor","label":"Outline Color","kind":"COLOR","size":4,"default":[0.6000000238418579,0.6000000238418579,0.6000000238418579,0.10000000149011612],"gamma":true},{"name":"_OutlineShadowColor","label":"Outline Shadow Color","kind":"COLOR","size":4,"default":[0.6000000238418579,0.6000000238418579,0.6000000238418579,1],"gamma":true}]},{"name":"Stocking","gate":null,"rows":[{"name":"_UseStockingFalloff","label":"Use Stocking Falloff","kind":"SWITCH","size":1,"default":[0]},{"name":"_AnisotropicGXX","label":"Anisotropic GGX","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_UseGlitter","label":"Use Stocking Glitter ","kind":"SWITCH","size":1,"default":[0]},{"name":"_GlitterDensity","label":"Glitter Density","kind":"SLIDER","size":1,"min":1,"max":200,"default":[20]},{"name":"_GlitterRimFalloff","label":"Glitter Rim Falloff","kind":"SLIDER","size":1,"min":0.1,"max":10,"default":[1]},{"name":"_GlitterViewWeight","label":"Glitter View Weight","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.05000000074505806]},{"name":"_GlitterRimMin","label":"Glitter Rim Center","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0]},{"name":"_GlitterRimMax","label":"Glitter Rim Side","kind":"SLIDER","size":1,"min":0.5001,"max":2,"default":[1]},{"name":"_GlitterRimIntensity","label":"Glitter Rim Intensity","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_GlitterSpecIntensity","label":"Glitter Spec Intensity","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_GlitterSpeed","label":"Glitter Speed","kind":"SLIDER","size":1,"min":0,"max":10,"default":[1]},{"name":"_GlitterRoughness","label":"Glitter Roughness\t","kind":"SLIDER","size":1,"min":-10,"max":10,"default":[0]},{"name":"_GlitterMetallic","label":"Glitter Metallic","kind":"SLIDER","size":1,"min":-10,"max":10,"default":[0]},{"name":"_GlitterCurvature","label":"Glitter Curvature","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_StencilComp","label":"Stencil Comparison","kind":"VALUE","size":1,"default":[0]},{"name":"_StencilOp","label":"Stencil Operation","kind":"VALUE","size":1,"default":[0]},{"name":"_StencilRefH","label":"_StencilRefH","kind":"VALUE","size":1,"default":[203]},{"name":"_StencilRefE","label":"_StencilRefE","kind":"VALUE","size":1,"default":[204]},{"name":"_StencilRefF","label":"_StencilRefF","kind":"VALUE","size":1,"default":[200]},{"name":"_StencilRefC","label":"_StencilRefC","kind":"VALUE","size":1,"default":[206]},{"name":"_WriteMask","label":"_WriteMask","kind":"VALUE","size":1,"default":[255]},{"name":"_StencilRefCharStart","label":"_StencilRefCharStart","kind":"VALUE","size":1,"default":[200]},{"name":"_DirOutlineWidthExt","label":"DirOutline Width Extend","kind":"SLIDER","size":1,"min":0,"max":0.3,"default":[0]},{"name":"_CampColorIndex","label":"Camp Color Index","kind":"VALUE","size":1,"default":[0]},{"name":"_UseGIFlatten","label":"Use GI Flatten","kind":"SWITCH","size":1,"default":[0]},{"name":"_OutlineZBias","label":"Outline Z-Bias","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_OutlineIntensity","label":"Outline Intensity","kind":"SLIDER","size":1,"min":1,"max":30,"default":[1]},{"name":"_AnisotropicSpecular","label":"Use Anisotropic Specular","kind":"SWITCH","size":1,"default":[0]},{"name":"_AdjustShadowBias","label":"Adjust Shadow Bias","kind":"SWITCH","size":1,"default":[0]},{"name":"_ShadowBiasDistance","label":"Shadow Bias Distance","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.10000000149011612]},{"name":"_UseSpecularUV2","label":"Use UV2","kind":"SWITCH","size":1,"default":[0]},{"name":"_UseBlendTex","label":"Use Blend Tex (FaceSDF)","kind":"SWITCH","size":1,"default":[0]},{"name":"_BlendSmoothness","label":"Blend Smoothness","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.10000000149011612]},{"name":"_UseFurShell","label":"Use Fur Shell","kind":"SWITCH","size":1,"default":[0]},{"name":"_FurShellThickness","label":"Fur Shell Thickness","kind":"SLIDER","size":1,"min":0.01,"max":5,"default":[0.5]},{"name":"_UseVolumetricEffect","label":"Use Volumetric Effect","kind":"SWITCH","size":1,"default":[0]},{"name":"_BaseInsideLerp","label":"BaseInsideLerp","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_InsideBaseColor","label":"InsideBaseColor","kind":"HDRCOLOR","size":4,"default":[1,1,1,0]},{"name":"_InsideColorContrast","label":"InsideColorContrast","kind":"SLIDER","size":1,"min":0,"max":15,"default":[1]},{"name":"_InsideColorBias","label":"InsideColorBias","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0.5]},{"name":"_InsideHeightContrast","label":"InsideHeightContrast","kind":"SLIDER","size":1,"min":0,"max":2,"default":[1]},{"name":"_InsideHeightBias","label":"InsideHeightBias","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_FakeIntensity","label":"FakeIntersity","kind":"SLIDER","size":1,"min":0,"max":2,"default":[0.25]},{"name":"_ReflectionIntensity","label":"ReflectionIntensity","kind":"SLIDER","size":1,"min":0,"max":5,"default":[0.3499999940395355]},{"name":"_ReflectionFresnelF0","label":"ReflectionFresnelF0","kind":"SLIDER","size":1,"min":-1,"max":1,"default":[0]},{"name":"_UseMatcapRef","label":"_UseMatcapRef","kind":"SWITCH","size":1,"default":[0]},{"name":"_MatcapIntensity","label":"Matcap Intensity","kind":"SLIDER","size":1,"min":0.1,"max":10,"default":[1]},{"name":"_MatcapRimPower","label":"Matcap Rim Power","kind":"SLIDER","size":1,"min":0.01,"max":1,"default":[1]},{"name":"_UseDetailMap","label":"Use Detail Map","kind":"SWITCH","size":1,"default":[0]},{"name":"_DetailAlphaMode","label":"Detail Alpha Mode","kind":"VALUE","size":1,"default":[1]},{"name":"_DetailAlphaIntensity","label":"Detail Alpha Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_DetailAlbedoIntensity","label":"Detail Albedo Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_DetailNormalIntensity","label":"Detail Normal Intensity","kind":"SLIDER","size":1,"min":0,"max":2,"default":[0]},{"name":"_DetailRMIntensity","label":"Detail RM Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_AdditionalLightShadow","label":"Additional Light Shadow","kind":"SLIDER","size":1,"min":0,"max":1,"default":[1]},{"name":"_UseBillboard","label":"Use Billboard","kind":"SWITCH","size":1,"default":[0]}]},{"name":"Character Effect","gate":null,"rows":[{"name":"_FinalTint","label":"Final Tint","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_AoeSelect","label":"Aoe Select","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_AoeSelectColor","label":"Aoe Select Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_DissolveIntensity","label":"Dissolve Lerp","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_EnableHolographicScanline","label":"_EnableHolographicScanline","kind":"VALUE","size":1,"default":[0]},{"name":"_HolographicColor","label":"Holographic Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_HolographicIntensity","label":"Holographic Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_HolographicWidth","label":"Holographic Width","kind":"VALUE","size":1,"default":[200]},{"name":"_ConcealLerp","label":"Conceal Lerp","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_Tutorial","label":"Tutorial","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_TutorialColor","label":"Tutorial Color","kind":"COLOR","size":4,"default":[1,1,1,1],"gamma":true},{"name":"_OnHitColor","label":"On Hit Color","kind":"COLOR","size":4,"default":[0,0,0,1],"gamma":true},{"name":"_CharSaturation","label":"Char Saturation","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0]},{"name":"_PaintInfluence","label":"_PaintInfluence","kind":"VALUE","size":1,"default":[0]},{"name":"_ColorOffset","label":"_ColorOffset","kind":"VALUE","size":1,"default":[0]},{"name":"_Moisture","label":"_Moisture","kind":"VALUE","size":1,"default":[0]},{"name":"_WetFlowStrength","label":"_WetFlowStrength","kind":"VALUE","size":1,"default":[0]},{"name":"_WetTraceStrength","label":"_WetTraceStrength","kind":"VALUE","size":1,"default":[0]},{"name":"_WetFlowSpeed","label":"_WetFlowSpeed","kind":"VALUE","size":1,"default":[0]},{"name":"_WetFlowSize","label":"_WetFlowSize","kind":"VALUE","size":1,"default":[0]},{"name":"_FaceLightDirAdjustment","label":"_WetFlowSize","kind":"VALUE","size":1,"default":[0]},{"name":"_HairDummyDirection","label":"_HairDummyDirection","kind":"VECTOR","size":4,"default":[0,0,0,0.15000000596046448]},{"name":"_HairDummyPosition","label":"_HairDummyPosition","kind":"VECTOR","size":4,"default":[0,1,0,0.15000000596046448]},{"name":"_ShadowIntensity","label":"Shadow Intensity","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.25]},{"name":"_CorneaParallax","label":"Cornea Parallax","kind":"SLIDER","size":1,"min":0,"max":0.5,"default":[0.30000001192092896]},{"name":"_SpecularParallax","label":"Specular Parallax","kind":"SLIDER","size":1,"min":0,"max":1,"default":[0.30000001192092896]},{"name":"_QueueOffset","label":"Queue offset","kind":"VALUE","size":1,"default":[1]},{"name":"_MainColor","label":"Main Color","kind":"COLOR","size":4,"default":[1,1,1,0.8500000238418579],"gamma":true}]}],"srgb_params":["_BaseColor","_StockingCenterColor","_StockingFalloffColor","_EmissionColor","_OutlineTintColor","_FresnelColor","_AddMatcapColor","_AnisotropyHueColor","_BrightenColor","_ColorA","_ColorB","_ColorG","_ColorMax","_ColorMaxR","_ColorMin","_ColorMinR","_DarkColor","_DayColor","_DyeingColor","_DyeingColor2","_DyeingColor3","_DyeingColor4","_DyeingColorA","_DyeingColorAMax","_DyeingColorAMin","_DyeingColorB","_DyeingColorG","_DyeingColorR","_ExpressionMaskColor","_EyebrowDarkColor","_HighLightColor1","_HighLightColor2","_MakeupColor","_MaskColor02","_MaskColor03","_MaskColor04","_MaskColor05","_MaskColor06","_MaskColor07","_MaskColor08","_PaintHighlightDayColor","_PartColor","_RampColor","_RampPartColor","_RimColor","_RimLightColor","_SiwaColor","_SkinColor","_SpecularColor","SkinColor","KuroCharacterAmbientColor","KuroCharacterMainLightColor","NearCharLightColor","NearCharShadowColor","KuroCharacterRimColor","KuroToonRimColorScale","SubsurfaceColor","SkinSubsurfaceColor","_CutsceneBaseColor","_RainColor","_DecalBaseColor","_FogColor","_RefractTint","_SubsurfaceColor","_SequenceBaseColorMapTint","_ColorTop","_ColorBottom","_CubeMapColor","_MainTex2Color","_InkColor","_HeightColorGradientColor","_HeightColorGradientColor2","_RBMainColorMask","_RBOffsetColorMask","_RBOffset1ColorMask","_RBOffset2ColorMask","_Color","_HighLightColor","_EmmissiveColor","_DissolveEmissiveColor","_BrightColor","_ScanFillColor","_ColorFlat","_ShadowColor","_CapsuleAoColor","_Metallic","_SDFRimColor","_FaceDecalTintColor","_EyeTintColor","_AnisotropyColor2","_HairBaseTintColor","_HairAddTintColor","_FurColor","_ClearCoatColor","_ParallaxColor","_SilkStockingsColor","_SilkStockingsDryColor","_SilkStockingsWetColor","_AnisotropyColorAdditional","_ExtraRootTintColor","_ExtraDepthTintColor","_CustomizeBaseColor","_CustomizeBaseTintColor","_CustomizeAddTintColor","_ErosionBaseColor","_ErosionBaseRootColor","_ErosionBaseTopColor","_PuppetPDCurveBaseColor","_PuppetPDCurveLightColor","_PuppetPDCurveEdgeColor","_PuppetBaseColor","_PuppetPatternTintEdgeColor","_StylizedFresnelColor","_EnemyHitFlashBrightColor","_EnemyHitFlashFresnelColor","_VFXColor","_VFXBlendTint","_VFXFresnelColor","_TintColor","_BlendTint","_ColorAdjustmentColorBlend","_ColorAdjustmentRimColor","_OutlineColor","_OutlineShadowColor","_FinalTint","_AoeSelectColor","_HolographicColor","_TutorialColor","_OnHitColor","_MainColor"],"part_meta":{"Standard":{"id":0,"transparent":false,"blend":null,"cull":null,"outline":["GFOutline","GFCharTransOutline"],"stochastic_alpha":false,"shader":"gf_shader/pbr/character/uber","aliases":["gf_shader/pbr/character/ubertrans"],"discriminator":null},"Face":{"id":1,"transparent":false,"blend":null,"cull":null,"outline":["GFOutline"],"stochastic_alpha":false,"shader":"gf_shader/pbr/character/uber","aliases":[],"discriminator":"_UseBlendTex"},"Eyes":{"id":2,"transparent":false,"blend":null,"cull":null,"outline":[],"stochastic_alpha":false,"shader":"gf_shader/pbr/character/eye","aliases":[],"discriminator":null},"EyeBlendAdd":{"id":3,"transparent":true,"blend":null,"cull":null,"outline":[],"stochastic_alpha":false,"shader":"gf_shader/pbr/character/eyeblend_add","aliases":[],"discriminator":null},"EyeBlendMultiply":{"id":4,"transparent":true,"blend":[["fixed",2],["fixed",0]],"cull":null,"outline":[],"stochastic_alpha":false,"shader":"gf_shader/pbr/character/eyeblend_multiply","aliases":[],"discriminator":null}},"non_shading":[],"host_shadow_casters":true,"source_names":{},"cull":{"property":"_Cull","fixed":2,"two_sided":null},"host":{"registry_module":"RuriRipperImporter.Host.Blender.material_builder","register_fn":"register_graph_provider","unregister_fn":"unregister_graph_provider","register_vertex_stage_fn":"register_vertex_stage","unregister_vertex_stage_fn":"unregister_vertex_stage","register_post_stage_fn":"","unregister_post_stage_fn":"","load_image_fn":"_load_image","rig_identity_module":"RuriRipperImporter.Host.Blender.rig_identity","rig_bone_fn":"bone_for_unity_name","rig_unity_name_fn":"unity_name_for_bone","world_basis":[[-1,0,0],[0,0,-1],[0,1,0]],"world_basis_fn":"world_basis","register_level_globals_fn":"register_level_globals","unregister_level_globals_fn":"unregister_level_globals","register_volume_textures_fn":"register_volume_textures","unregister_volume_textures_fn":"unregister_volume_textures","volume_image_fn":"volume_image"},"post":null,"group_names":["Ruri GirlsFrontline Uber Standard s0","Ruri GirlsFrontline Uber Standard s1","Ruri GirlsFrontline Uber Standard s2","Ruri GirlsFrontline Uber Standard s3","Ruri GirlsFrontline Uber Face s0","Ruri GirlsFrontline Uber Face s1","Ruri GirlsFrontline Uber Face s2","Ruri GirlsFrontline Uber Face s3","Ruri GirlsFrontline Uber Eyes s0","Ruri GirlsFrontline Uber Eyes s1","Ruri GirlsFrontline Uber Eyes s2","Ruri GirlsFrontline Uber EyeBlendAdd s0","Ruri GirlsFrontline Uber EyeBlendAdd s1","Ruri GirlsFrontline Uber EyeBlendAdd s2","Ruri GirlsFrontline Uber EyeBlendMultiply s0","Ruri GirlsFrontline Uber EyeBlendMultiply s1","Ruri GirlsFrontline Uber Outline Standard s0"]},{"kernel":"rvg1","stamp":"e58771848619c111","names":{"panel_key":"ruri_post_girlsfrontline","panel_title":" \u53C2\u6570","template_mat":" Tpl ","vtx_modifier":" Vertex","vtx_tree_prefix":" Vertex ","material_name":"","st_slot":"_BaseMap","st_node":"RuriBaseMapST"},"blend":"ruri_post_girlsfrontline.blend","parts":{},"host_shadow_casters":true,"engine_global_sources":{},"level_images":{},"post":{"group":"Ruri GirlsFrontline Post","scene_tree":"Ruri GirlsFrontline Post Scene","color_in":"color","color_out":"ret","coords":[],"extra_in":["postExposureOffset","slopeOffset","toeOffset","shoulderOffset","blackClipOffset","whiteClipOffset"],"sizes":{"postExposureOffset":1,"slopeOffset":1,"toeOffset":1,"shoulderOffset":1,"blackClipOffset":1,"whiteClipOffset":1},"images":[],"passes":{},"chains":[],"grades":["ruri_character_uber_girlsfrontline"]},"host":{"registry_module":"RuriRipperImporter.Host.Blender.material_builder","register_fn":"register_graph_provider","unregister_fn":"unregister_graph_provider","register_vertex_stage_fn":"register_vertex_stage","unregister_vertex_stage_fn":"unregister_vertex_stage","register_post_stage_fn":"register_post_stage","unregister_post_stage_fn":"unregister_post_stage","load_image_fn":"_load_image","rig_identity_module":"","rig_bone_fn":"","rig_unity_name_fn":"","world_basis":null,"world_basis_fn":"world_basis","register_level_globals_fn":"register_level_globals","unregister_level_globals_fn":"unregister_level_globals","register_volume_textures_fn":"register_volume_textures","unregister_volume_textures_fn":"unregister_volume_textures","volume_image_fn":"volume_image"},"group_names":["Ruri GirlsFrontline Post"]}]''')

OWN_SAMPLER = 'TEXTURE'
OVERLAY_OUTPUT_PREFIX = '__overlay__'
OWN_SAMPLER_LENDER_SEPARATOR = ':'
OWN_SAMPLER_WRAPS = ('repeat', 'clamp', 'mirror', 'mirroronce')
OWN_SAMPLER_SOCKETS = ('__wrap_u', '__wrap_v', '__point')
SCREEN_SOCKETS = ('screen_x', 'screen_y')
VIEW_WINDOW = {'columns': ('ruri_view_window_0', 'ruri_view_window_1', 'ruri_view_window_2', 'ruri_view_window_3'), 'inverse_columns': ('ruri_view_window_inverse_0', 'ruri_view_window_inverse_1', 'ruri_view_window_inverse_2', 'ruri_view_window_inverse_3'), 'clip': 'ruri_view_clip', 'screen': 'ruri_view_screen'}
MAP_UV_EXTENSIONS = {'EXTEND': 'Extend', 'REPEAT': 'Repeat', 'CLIP': 'Clip'}
MAP_UV_FILTERS = {'Linear': 'Bilinear', 'Closest': 'Nearest'}

def _sampler_lender(extension):
    prefix = OWN_SAMPLER + OWN_SAMPLER_LENDER_SEPARATOR
    if isinstance(extension, str) and extension.startswith(prefix):
        return extension[len(prefix):]
    return None


GENERATED_FRAME_NAME = 'RuriGenerated'
GENERATED_FRAME_LABEL = '此Shader为Ruri自循环管线生成的节点 请勿编辑'
GENERATED_FRAME_LABEL_SIZE = 64


def _frame_generated(tree):
    frame = tree.nodes.get(GENERATED_FRAME_NAME)
    if frame is None:
        frame = tree.nodes.new('NodeFrame')
        frame.name = GENERATED_FRAME_NAME
        frame.label = GENERATED_FRAME_LABEL
        frame.label_size = GENERATED_FRAME_LABEL_SIZE
    orphans = [node for node in tree.nodes if node.parent is None and node.name != GENERATED_FRAME_NAME]
    for node in orphans:
        node.parent = frame


MATH_ARITY = {
    'ADD': 2,
    'SUBTRACT': 2,
    'MULTIPLY': 2,
    'DIVIDE': 2,
    'POWER': 2,
    'LOGARITHM': 2,
    'MINIMUM': 2,
    'MAXIMUM': 2,
    'LESS_THAN': 2,
    'GREATER_THAN': 2,
    'MODULO': 2,
    'FLOORED_MODULO': 2,
    'SNAP': 2,
    'PINGPONG': 2,
    'ARCTAN2': 2,
    'MULTIPLY_ADD': 3,
    'COMPARE': 3,
    'SMOOTH_MIN': 3,
    'SMOOTH_MAX': 3,
    'WRAP': 3,
    'SQRT': 1,
    'INVERSE_SQRT': 1,
    'ABSOLUTE': 1,
    'EXPONENT': 1,
    'SIGN': 1,
    'ROUND': 1,
    'FLOOR': 1,
    'CEIL': 1,
    'TRUNC': 1,
    'FRACT': 1,
    'RADIANS': 1,
    'DEGREES': 1,
    'SINE': 1,
    'COSINE': 1,
    'TANGENT': 1,
    'SINH': 1,
    'COSH': 1,
    'TANH': 1,
    'ARCSINE': 1,
    'ARCCOSINE': 1,
    'ARCTANGENT': 1,
}

VECT_ARITY = {
    'ADD': (0, 1),
    'SUBTRACT': (0, 1),
    'MULTIPLY': (0, 1),
    'DIVIDE': (0, 1),
    'CROSS_PRODUCT': (0, 1),
    'PROJECT': (0, 1),
    'REFLECT': (0, 1),
    'DOT_PRODUCT': (0, 1),
    'DISTANCE': (0, 1),
    'MODULO': (0, 1),
    'SNAP': (0, 1),
    'MINIMUM': (0, 1),
    'MAXIMUM': (0, 1),
    'POWER': (0, 1),
    'MULTIPLY_ADD': (0, 1, 2),
    'WRAP': (0, 1, 2),
    'FACEFORWARD': (0, 1, 2),
    'REFRACT': (0, 1, 3),
    'SCALE': (0, 3),
    'NORMALIZE': (0,),
    'LENGTH': (0,),
    'ABSOLUTE': (0,),
    'SIGN': (0,),
    'ROUND': (0,),
    'FLOOR': (0,),
    'CEIL': (0,),
    'FRACTION': (0,),
    'SINE': (0,),
    'COSINE': (0,),
    'TANGENT': (0,),
}

# 宿主世界基(真源世界 -> 宿主世界,行优先 b = M.u)。着色内核在真源世界系里算,运行时建的
# 兑现物(灯方向、环境询问的法线/方向)经它进出,与生成期物化的模板组同一套约定。
# 由栈清单声明,注册时与宿主模块核对;没声明就不该有任何世界量要换,碰到即响亮失败。
_WORLD_BASIS = []


def declare_world_basis(rows):
    """栈清单里的世界基。多个栈声明必须一致 -- 一个会话只有一个宿主世界系。"""
    if not rows:
        return
    stated = [[float(value) for value in row] for row in rows]
    if _WORLD_BASIS and _WORLD_BASIS[0] != stated:
        raise RuntimeError('[Ruri] 栈清单的宿主世界基互相矛盾: {0} vs {1}'.format(_WORLD_BASIS[0], stated))
    _WORLD_BASIS[:] = [stated]


def _world_basis():
    if not _WORLD_BASIS:
        raise RuntimeError('[Ruri] 要换世界系,但没有任何栈声明宿主世界基(host.world_basis)')
    return _WORLD_BASIS[0]


def _host():
    """宿主模块(清单的 host.registry_module,全平台同一个)。插件数据的出生、清理与存盘守卫都住在那边:宿主自己
    也造插件数据块(视点、关卡图占位),同一条规矩只许一处声明。"""
    import importlib
    return importlib.import_module(MANIFESTS[0]['host']['registry_module'])


class G:
    """材质树接线器(运行时只在本地材质平面建小规模兑现物;模板全在 .blend 里)。"""

    def __init__(self, tree, is_group=False):
        self.t = tree
        self.is_group = is_group
        self._sep_cache = {}
        self._cse = {}
        self._geo = None
        self._texco = None
        self._tbn = None

    def _ck(self, *parts):
        out = []
        for p in parts:
            if isinstance(p, bpy.types.NodeSocket):
                out.append(p.as_pointer())
            elif isinstance(p, (tuple, list)):
                out.append(tuple(round(float(x), 9) for x in p))
            elif isinstance(p, (int, float)):
                out.append(round(float(p), 9))
            else:
                out.append(p)
        return tuple(out)

    def _nd(self, typ):
        nd = self.t.nodes.new(typ)
        mapping = getattr(nd, 'texture_mapping', None)
        if mapping is not None:
            # 宿主新建的纹理节点 TexMapping 不带单位阵标记,每次采样前多发一个恒等 mapping_mat4、占七个 node_tree
            # uniform;整块超过显卡 UBO 上限(64KB)宿主就不建这块 UBO,材质里所有常量读 0。写一次平移触发重算即认出恒等。
            mapping.translation = mapping.translation
        return nd

    def layout(self):
        """整棵树按建树序一次摆成 40 列的网格,摆完收进生成框。宿主的每一次属性写(位置也算)都把整棵树重算一遍,
        逐个节点写位置就是节点数 × 整棵树;foreach_set 一次写完、不触发树更新。生成框钉在原点:框里节点的位置
        是相对框的,框占一格网格就把整片挪走。"""
        nodes = self.t.nodes
        flat = []
        slot = 0
        for node in nodes:
            if node.name == GENERATED_FRAME_NAME:
                flat.extend((0.0, 0.0))
                continue
            flat.extend(((slot % 40) * 200.0, -(slot // 40) * 240.0))
            slot += 1
        nodes.foreach_set('location', flat)
        _frame_generated(self.t)

    def _set(self, sock, v):
        if isinstance(v, bpy.types.NodeSocket):
            self.t.links.new(v, sock)
            return
        if not isinstance(v, (int, float)):
            v = v[0] if sock.type not in ('RGBA', 'VECTOR') else v
        if sock.type == 'RGBA':
            value = (v, v, v, 1.0) if isinstance(v, (int, float)) else (v[0], v[1], v[2], 1.0)
        elif sock.type == 'VECTOR':
            value = (v, v, v) if isinstance(v, (int, float)) else (v[0], v[1], v[2])
        elif sock.type == 'INT':
            value = int(v)
        elif sock.type == 'BOOLEAN':
            value = bool(v)
        else:
            value = float(v)
        # 与口上现值相同就不写:每写一次宿主都把整棵树重算一遍。
        current = sock.default_value
        same = tuple(current) == value if hasattr(current, '__len__') else current == value
        if not same:
            sock.default_value = value

    def math(self, op, a, b=0.0, c=0.0, clamp=False):
        # 三操作数算子必须显式设满(第三口默认 0.5);只读 1~2 个操作数的算子按表精确写。
        n = MATH_ARITY.get(op, 3)
        ops = (a, b, c)[:n]
        k = ('m', op, bool(clamp), self._ck(*ops))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeMath')
        nd.operation = op
        nd.use_clamp = bool(clamp)
        for i in range(n):
            self._set(nd.inputs[i], ops[i])
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def vmath(self, op, a, b=(0.0, 0.0, 0.0), c=(0.0, 0.0, 0.0), s=1.0):
        if op == 'SCALE' and isinstance(s, bpy.types.NodeSocket):
            # 宿主给 VectorMath 的四个口都发 uniform(不读的也发):按插口缩放改乘广播向量,同式同舍入,少占一个向量。
            if isinstance(a, (tuple, list)) and tuple(float(x) for x in a) == (1.0, 1.0, 1.0):
                return self.bc(s)
            return self.vmath('MULTIPLY', a, self.bc(s))
        idx = VECT_ARITY.get(op, (0, 1, 2, 3))
        vals = (a, b, c, s)
        k = ('v', op, self._ck(*[vals[i] for i in idx]))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeVectorMath')
        nd.operation = op
        for i in idx:
            self._set(nd.inputs[i], vals[i])
        out = nd.outputs[1] if op in ('LENGTH', 'DOT_PRODUCT', 'DISTANCE') else nd.outputs[0]
        self._cse[k] = out
        return out

    def mixf(self, fac, a, b):
        # clamp_factor 默认 True 会钳 lerp 的 t,必须关。fac=0→a, 1→b。
        k = ('mf', self._ck(fac, a, b))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeMix')
        nd.data_type = 'FLOAT'
        nd.clamp_factor = False
        self._set(nd.inputs[0], fac)
        self._set(nd.inputs[2], a)
        self._set(nd.inputs[3], b)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def mixv(self, fac, a, b):
        k = ('mv', self._ck(fac, a, b))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeMix')
        nd.data_type = 'VECTOR'
        nd.factor_mode = 'UNIFORM'
        nd.clamp_factor = False
        self._set(nd.inputs[0], fac)
        self._set(nd.inputs[4], a)
        self._set(nd.inputs[5], b)
        self._cse[k] = nd.outputs[1]
        return nd.outputs[1]

    def choosev(self, cond, a, b):
        # 按 0/1 条件取一端(cond=0→a, 1→b),没选中那端零影响:DIVIDE 是 b≠0 ? a/b : 0 的三元式,死端的 Inf/NaN 不外泄。
        keep_b = self.comb(cond, cond, cond)
        other = self.math('SUBTRACT', 1.0, cond)
        keep_a = self.comb(other, other, other)
        return self.vmath('ADD', self.vmath('DIVIDE', a, keep_a), self.vmath('DIVIDE', b, keep_b))

    def choosef(self, cond, a, b):
        # choosev 的标量版。取一端不能用 mixf:GPU 的 mix 是 a + t·(b − a),一端是 1e30 这类缺席值时另一端被整个抵消成 0。
        keep_a = self.math('SUBTRACT', 1.0, cond)
        return self.math('ADD', self.math('DIVIDE', a, keep_a), self.math('DIVIDE', b, cond))

    def clampn(self, x, mn=0.0, mx=1.0):
        k = ('cl', self._ck(x, mn, mx))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeClamp')
        self._set(nd.inputs[0], x)
        self._set(nd.inputs[1], mn)
        self._set(nd.inputs[2], mx)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def sep(self, v):
        key = v.as_pointer() if isinstance(v, bpy.types.NodeSocket) else None
        if key is not None and key in self._sep_cache:
            return self._sep_cache[key]
        nd = self._nd('ShaderNodeSeparateXYZ')
        self._set(nd.inputs[0], v)
        r = (nd.outputs[0], nd.outputs[1], nd.outputs[2])
        if key is not None:
            self._sep_cache[key] = r
        return r

    def comb(self, x, y, z):
        k = ('cb', self._ck(x, y, z))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeCombineXYZ')
        self._set(nd.inputs[0], x)
        self._set(nd.inputs[1], y)
        self._set(nd.inputs[2], z)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def bc(self, s):
        if isinstance(s, (int, float)):
            return (s, s, s)
        return self.comb(s, s, s)

    def vtrans(self, v, frm, to, kind='VECTOR'):
        k = ('vt', frm, to, kind, self._ck(v))
        hit = self._cse.get(k)
        if hit is not None:
            return hit
        nd = self._nd('ShaderNodeVectorTransform')
        nd.vector_type = kind
        nd.convert_from = frm
        nd.convert_to = to
        # 按名取:5.3 起首个输入是只在内部着色树可用的 LightIndex,Vector 退到第二位。
        self._set(nd.inputs['Vector'], v)
        self._cse[k] = nd.outputs[0]
        return nd.outputs[0]

    def _basis(self, v, rows):
        comps = self.sep(v)
        parts = []
        for row in rows:
            total = None
            for weight, comp in zip(row, comps):
                if weight == 0.0:
                    continue
                term = comp if weight == 1.0 else self.math('MULTIPLY', comp, weight)
                total = term if total is None else self.math('ADD', total, term)
            parts.append(0.0 if total is None else total)
        return self.comb(parts[0], parts[1], parts[2])

    def b2u(self, v, point=False):
        # 宿主世界 -> 真源世界:世界基的逆(正交,转置)。点与向量同式 -- 基里没有平移。
        _ = point
        rows = _world_basis()
        return self._basis(v, [[rows[j][i] for j in range(3)] for i in range(3)])

    def u2b(self, v):
        return self._basis(v, _world_basis())

    def geo(self):
        if self._geo is None:
            self._geo = self._nd('ShaderNodeNewGeometry')
        return self._geo

    def facing_sign(self):
        """这一片元看到的是正面 +1、背面 -1 —— 真源光栅宿主的 VFACE。"""
        hit = self._cse.get(('facing_sign',))
        if hit is None:
            hit = self.math('SUBTRACT', 1.0, self.math('MULTIPLY', self.geo().outputs['Backfacing'], 2.0))
            self._cse[('facing_sign',)] = hit
        return hit

    def surface_normal(self):
        """插值法线的原样(世界空间,不随被看到的是哪一面翻转)。宿主在背面把 Geometry 的法线整个取反
        (EEVEE:eevee_surf_common 的 init_globals;Cycles:shader_setup 同样取反),真源光栅宿主不翻:
        要不要按面翻、翻哪一条,是每个真源着色器自己拿 VFACE 决定的(有的只翻菲涅尔那一条,有的一条都不翻)。
        这里乘 VFACE 翻回来,内核按真源自己的判断去翻。"""
        hit = self._cse.get(('surface_normal',))
        if hit is None:
            hit = self.vmath('SCALE', self.geo().outputs['Normal'], s=self.facing_sign())
            self._cse[('surface_normal',)] = hit
        return hit

    def texco(self):
        if self._texco is None:
            self._texco = self._nd('ShaderNodeTexCoord')
        return self._texco

    def attr(self, name):
        nd = self._nd('ShaderNodeAttribute')
        nd.attribute_name = name
        return nd

    def layer_attr(self, name):
        nd = self._nd('ShaderNodeAttribute')
        nd.attribute_type = 'VIEW_LAYER'
        nd.attribute_name = name
        return nd

    def _tbn_axis(self, x, y, z):
        nd = self._nd('ShaderNodeNormalMap')
        nd.space = 'TANGENT'
        nd.uv_map = ''
        self._set(nd.inputs['Strength'], 1.0)
        self._set(nd.inputs['Color'], (x * 0.5 + 0.5, y * 0.5 + 0.5, z * 0.5 + 0.5))
        return nd.outputs['Normal']

    def tbn(self):
        """(tangentWS, w) —— **宿主自己的**切线基,不读任何烘焙网格属性。

        Normal Map 节点(TANGENT 空间、Strength=1)算的就是 normalize(T*x + B*y + N*z),
        其中 T/B 是它按活动 UV 现算的 MikkTSpace 基,B 已经带着 bitangent_sign。所以喂
        (1,0,0) 出来的是 T、喂 (0,1,0) 出来的是 B,两个都在世界空间。背面的片元上节点把整组基取反
        (EEVEE 先翻 T 再用翻过的 N 叉出 B;Cycles 用原样的基算完再整个取反),T 与 B 同乘 VFACE
        翻回原样,与 surface_normal 同一个口径。

        w 取的是 **Unity 口径**,不是 Blender 口径:段图把 tangentWS 先换到 Unity 空间
        (WORLD→OBJECT 的 VectorTransform + Y/Z 对调)**再**做 cross(N,T)*w。那次换轴是反射
        (det = -1),叉积随之反号 ⇒ w_unity = -w_blender。所以这里反解写 cross(T,N):
        w = sign(dot(cross(T,N), B)) == -sign(dot(cross(N,T), B))。
        判据不是推导:在生成的段组里从 input_tangentWS 走一遍,第一跳就是那个
        WORLD→OBJECT 的 VectorTransform,第三跳 CombineXYZ 之后才 CROSS_PRODUCT。

        为什么不读属性:读属性要求每张网格事先烘好 ruri_tangent/ruri_tangent_sign,而
        Attribute 节点**缺属性与读到零向量无法区分**,两者都静默把切线基压塌 —— 法线贴图
        整条失效而一个字不报。改过拓扑的网格会保留属性名把新增 corner 填零,于是「属性在」
        这个判据也跟着失效。基由宿主现算就没有这一类失效面,也不再有第二份切线真源。"""
        if self._tbn is None:
            tangent = self.vmath('SCALE', self._tbn_axis(1.0, 0.0, 0.0), s=self.facing_sign())
            bitangent = self.vmath('SCALE', self._tbn_axis(0.0, 1.0, 0.0), s=self.facing_sign())
            normal = self.surface_normal()
            w = self.math('SIGN', self.vmath(
                'DOT_PRODUCT', self.vmath('CROSS_PRODUCT', tangent, normal), bitangent))
            self._tbn = (tangent, w)
        return self._tbn

    ENV_PREFILTER_TAPS = (
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 0.5), (-1.0, 0.0, 0.5), (0.0, 1.0, 0.5), (0.0, -1.0, 0.5),
        (0.7071, 0.7071, 0.35), (-0.7071, 0.7071, 0.35),
        (0.7071, -0.7071, 0.35), (-0.7071, -0.7071, 0.35),
    )

    def _env_tap(self, image, projection, spec, strength, direction):
        if spec is not None:
            vector_type, location, rotation, scale = spec
            md = self._nd('ShaderNodeMapping')
            md.vector_type = vector_type
            md.inputs['Location'].default_value = location
            md.inputs['Rotation'].default_value = rotation
            md.inputs['Scale'].default_value = scale
            self._set(md.inputs['Vector'], direction)
            direction = md.outputs[0]
        nd = self._nd('ShaderNodeTexEnvironment')
        nd.image = image
        nd.projection = projection
        nd.interpolation = 'Linear'
        self._set(nd.inputs[0], direction)
        color = nd.outputs[0]
        if strength != 1.0:
            color = self.vmath('SCALE', color, s=strength)
        return color

    def env_image(self, image, direction, mip=None, spread=None):
        """锥形预滤波的等距环境采样(mip 按真源 mip↔粗糙度式反解;两者皆无 = 锐反射单抽样)。"""
        if mip is None and spread is None:
            return (self._env_tap(image, 'EQUIRECTANGULAR', None, 1.0, direction), 1.0)
        if spread is None:
            roughness = self.math('POWER', 2.0, self.math('DIVIDE', self.math('SUBTRACT', mip, 5.0), 1.2))
            spread = self.math('MINIMUM', self.math('MULTIPLY', roughness, roughness), 1.0)
        _dx, _dy, dz = self.sep(direction)
        polar = self.math('GREATER_THAN', self.math('ABSOLUTE', dz), 0.9)
        tangent = self.vmath('NORMALIZE', self.mixv(
            polar,
            self.vmath('CROSS_PRODUCT', direction, (0.0, 0.0, 1.0)),
            self.vmath('CROSS_PRODUCT', direction, (1.0, 0.0, 0.0))))
        bitangent = self.vmath('NORMALIZE', self.vmath('CROSS_PRODUCT', direction, tangent))
        total = None
        weight_sum = 0.0
        for offset_x, offset_y, weight in self.ENV_PREFILTER_TAPS:
            if offset_x or offset_y:
                offset = self.vmath('ADD',
                                    self.vmath('SCALE', tangent, s=offset_x),
                                    self.vmath('SCALE', bitangent, s=offset_y))
                tap = self.vmath('NORMALIZE',
                                 self.vmath('ADD', direction, self.vmath('SCALE', offset, s=spread)))
            else:
                tap = direction
            sample = self._env_tap(image, 'EQUIRECTANGULAR', None, 1.0, tap)
            total = (self.vmath('SCALE', sample, s=weight) if total is None
                     else self.vmath('ADD', total, self.vmath('SCALE', sample, s=weight)))
            weight_sum += weight
        return (self.vmath('SCALE', total, s=1.0 / weight_sum), 1.0)


# ==================== 图像纪律(全部踩过坑,逐条保命) ====================

def _linear_to_srgb(c):
    """_srgb_to_linear 的**精确逆**——两支都要对上,否则「作者值→uniform→作者值」的往返
    在 HDR 段(≥1)不闭合,写回材质会每次都读出一个新数,依赖图永远脏。"""
    c = float(c)
    if c <= 0.0031308:
        return 12.92 * c
    if c < 1.0:
        return 1.055 * (c ** (1.0 / 2.4)) - 0.055
    return c ** (1.0 / 2.2)


def _srgb_to_linear(c):
    """Unity 上传材质 Color 属性时做的那一次线性化(GammaToLinearSpace 逐字:≥1 走 2.2 次幂,
    HDR 颜色的超一值域正靠这一支)。清单的 srgb_params 说哪些属性要过这里。"""
    c = float(c)
    if c <= 0.04045:
        return c / 12.92
    if c < 1.0:
        return ((c + 0.055) / 1.055) ** 2.4
    return c ** 2.2


def _image_stored(image):
    # pack() 是唯一能清 dirty 的合法手段,否则关文件弹「N 张图片未保存」。
    try:
        image.pack()
    except Exception:
        pass


def _set_colorspace(image, want):
    # 写色彩空间会丢弃并重建像素缓冲(generated 图被抹黑;真图失效全部使用者 = O(N²))。
    # 唯一幂等写法 = 先比后写;每一处设色彩空间都必须走这里。
    # 带 ruri_colorspace_stated 的图是宿主按**资产自己声明的**色彩空间定死的,槽语义不许改写它:
    # 一张图可以同时挂在法线槽和自发光槽上,而色彩空间住在共享数据块上,按槽写就是后写的赢。
    if image is None:
        return
    try:
        if image.get('ruri_colorspace_stated'):
            return
    except Exception:
        pass
    try:
        if image.colorspace_settings.name != want:
            image.colorspace_settings.name = want
    except Exception:
        pass


def _slot_of(image_name):
    base, dot, tail = image_name.rpartition('.')
    return base if dot and tail.isdigit() else image_name


def _fix_two_channel_layout(real):
    """BC5 类双通道容器(R恒白/G=B=X/A=Y)就地还原 R<-G;只对 Non-Color 调用。"""
    if real.get('ruri_rg_layout_fixed'):
        return
    real['ruri_rg_layout_fixed'] = 1
    try:
        import numpy as _np
        w, h = real.size
        if w and h:
            buf = _np.empty(w * h * 4, dtype=_np.float32)
            real.pixels.foreach_get(buf)
            px = buf.reshape(-1, 4)
            if (float(px[:, 0].mean()) > 0.99 and float(px[:, 0].std()) < 0.02
                    and float(px[:, 1].std()) > 1e-3
                    and float(_np.abs(px[:, 1] - px[:, 2]).mean()) < 0.01):
                px[:, 0] = px[:, 1]
                real.pixels.foreach_set(buf)
                real.update()
                if real.packed_file is not None:
                    real.pack()
                print('[ruri-uber] {0}: 双通道导出布局已恢复 R<-G'.format(real.name), flush=True)
    except Exception as exc:
        print('[ruri-uber] {0} 通道布局检测失败: {1}'.format(real.name, exc), flush=True)


def _swap_image(node, real, images):
    # 色彩空间跟着占位图走(生成期按真源 .meta 定死);先比后写,见 _set_colorspace。
    non_color = node.image is not None and node.image.colorspace_settings.name == 'Non-Color'
    node.image = real
    _set_colorspace(real, 'Non-Color' if non_color else 'sRGB')
    if non_color:
        _fix_two_channel_layout(real)
    if node.get(OWN_SAMPLER_KEY):
        _apply_own_sampler(node, _sampler_image(node, real, images))


def _retire_materials(replaced, doomed=()):
    """{旧材质: 新材质}(用户已经换到新的上,见 compile_all):旧的整批删掉,名字还给新的;doomed 是同一批要删的
    别的数据块(量常量块用的拷贝)。删除整批一次 —— 逐张删每张都要把整个文件的 ID 引用走一遍(九千对象的关卡上
    一遍约 80 ms)。换用户也别逐个槽去换:对象级槽每换一格,宿主都把全部对象的材质表重核一遍,九千格就是半分钟;
    user_remap 一张一遍,不论持有者是谁。"""
    if not replaced and not doomed:
        return
    # 名字先按新材质记下:整批删掉之后旧材质的 python 句柄全部失效,再拿它们当键就查不到了。
    renames = [(new, old.name) for old, new in replaced.items()]
    bpy.data.batch_remove(list(replaced) + list(doomed))
    for new, name in renames:
        new.name = name


# 图节点上的这一格:1 = 按自己绑的图采;槽名 = 借那个槽绑的图的采样器(真源 SAMPLE_TEXTURE2D(甲, sampler_乙))。
OWN_SAMPLER_KEY = 'ruri_own_sampler'


def _sampler_image(node, real, images):
    # 采样状态读哪张图:自带的读自己那张,借来的读借出槽那张(那一槽没绑图 = 它的中性占位,宿主缺省状态)。
    lender = node.get(OWN_SAMPLER_KEY)
    if isinstance(lender, str):
        return (images or {}).get(lender)
    return real
# 宿主把贴图自己陈述的采样状态烙在图上(material_builder.SAMPLING_STATED_PROPERTY)。
SAMPLING_STATED_PROPERTY = 'ruri_sampling'
_NATIVE_EXTENSION = {'repeat': 'REPEAT', 'clamp': 'EXTEND', 'mirror': 'MIRROR'}


def _own_sampler_state(image):
    """(u 向寻址, v 向寻址, 是否点采样):图自己陈述的那份。没有陈述的图(用户自己的图、中性占位)
    带的就是 Blender 图像自己的状态 —— 重复寻址、线性过滤 —— 不是替谁猜的值。"""
    stated = image.get(SAMPLING_STATED_PROPERTY) if image is not None else None
    if stated is None:
        return OWN_SAMPLER_WRAPS[0], OWN_SAMPLER_WRAPS[0], False
    return str(stated['wrap_u']), str(stated['wrap_v']), str(stated['filter']) == 'point'


def _apply_own_sampler(node, image):
    """片元图节点按绑定那张图自带的采样器设寻址与过滤。

    两轴相同且图节点有原生等价(repeat/clamp/mirror):原生扩展方式,线性与点采样都精确。
    其余组合只在**点采样**下有精确等价:点采样不取 mip,逐轴在坐标上做寻址与硬件逐纹素相同 ——
    与顶点腿 own_sampler_fetch 同一套折法,图节点重复寻址。线性过滤带 mip,坐标上的折法会改 mip 的选择,
    没有精确等价物,响亮拒绝而不是挑一个近似。"""
    wrap_u, wrap_v, point = _own_sampler_state(image)
    # 先比后写:宿主每写一次节点属性都把整棵树重算一遍,换图时这几格多半没变。
    interpolation = 'Closest' if point else 'Linear'
    if node.interpolation != interpolation:
        node.interpolation = interpolation
    native = _NATIVE_EXTENSION.get(wrap_u) if wrap_u == wrap_v else None
    if native is not None:
        if node.extension != native:
            node.extension = native
        _axis_addressing(node, None, None, image)
        return
    if not point:
        raise RuntimeError('[ruri-uber] 贴图 {0} 自带的寻址是 u={1} v={2} 且线性过滤:Blender 图像节点的扩展方式'
                           '两轴共用且带 mip,这一组没有精确等价物。'.format(
                               image.name if image is not None else '?', wrap_u, wrap_v))
    if node.extension != 'REPEAT':
        node.extension = 'REPEAT'
    _axis_addressing(node, wrap_u, wrap_v, image)


AXIS_ADDRESSING_KEY = 'ruri_axis_addressing'


def _axis_addressing(node, wrap_u, wrap_v, image):
    """图节点坐标口前的逐轴寻址子图:重复原样、钳制夹到首末纹素中心、镜像按周期 2 折回再夹、
    单次镜像取绝对值再夹(与顶点腿 own_sampler_fetch 逐式相同)。子图的节点都烙着图节点的名字,
    换图时整段拆掉按新图的尺寸重建;wrap_u 为 None = 只拆不建,把原来的坐标源接回去。"""
    tree = node.id_data
    socket = node.inputs['Vector']
    source = socket.links[0].from_socket if socket.links else None
    stale = [one for one in tree.nodes if one.get(AXIS_ADDRESSING_KEY) == node.name]
    if stale:
        source = None
        for one in stale:
            if one.bl_idname == 'ShaderNodeSeparateXYZ' and one.inputs[0].links:
                upstream = one.inputs[0].links[0].from_socket
                if upstream.node.get(AXIS_ADDRESSING_KEY) != node.name:
                    source = upstream
    for one in stale:
        tree.nodes.remove(one)
    if wrap_u is None:
        # 没拆掉什么就什么都不接:原来的坐标源还在口上,重接一遍只是白让宿主重算整棵树。
        if stale and source is not None:
            tree.links.new(source, socket)
        return

    def made(kind):
        one = tree.nodes.new(kind)
        one[AXIS_ADDRESSING_KEY] = node.name
        one.location = (node.location.x - 220.0, node.location.y)
        return one

    def scalar(operation, a, b=None):
        one = made('ShaderNodeMath')
        one.operation = operation
        for index, value in enumerate((a, b)):
            if value is None:
                continue
            if isinstance(value, (int, float)):
                one.inputs[index].default_value = float(value)
            else:
                tree.links.new(value, one.inputs[index])
        return one.outputs[0]

    if source is None:
        source = made('ShaderNodeTexCoord').outputs['UV']
    axes = made('ShaderNodeSeparateXYZ')
    tree.links.new(source, axes.inputs[0])
    wrapped = made('ShaderNodeCombineXYZ')
    for axis, wrap in enumerate((wrap_u, wrap_v)):
        coordinate = axes.outputs[axis]
        half = 0.5 / max(float(image.size[axis]), 1.0)
        if wrap == 'repeat':
            result = coordinate
        elif wrap == 'clamp':
            result = scalar('MINIMUM', scalar('MAXIMUM', coordinate, half), 1.0 - half)
        elif wrap == 'mirror':
            folded = scalar('SUBTRACT', 1.0, scalar('ABSOLUTE', scalar('SUBTRACT', scalar(
                'MULTIPLY', scalar('FRACT', scalar('MULTIPLY', coordinate, 0.5)), 2.0), 1.0)))
            result = scalar('MINIMUM', scalar('MAXIMUM', folded, half), 1.0 - half)
        elif wrap == 'mirroronce':
            result = scalar('MINIMUM', scalar('MAXIMUM', scalar('ABSOLUTE', coordinate), half), 1.0 - half)
        else:
            raise RuntimeError('[ruri-uber] 贴图 {0} 陈述了未知的寻址 {1}'.format(image.name, wrap))
        tree.links.new(result, wrapped.inputs[axis])
    tree.links.new(wrapped.outputs[0], socket)
    _frame_generated(tree)


_NEUTRAL_IMAGES = {}


def _neutral_image(rgb, alpha, non_color):
    """1x1 图,颜色 = 割点自己声明的 neutral(缺图时该采到什么;与占位图的槽语义中性不是一回事)。
    插件数据:本会话一份,开文件时由 load pass 随材质重编现建。"""
    key = (tuple(round(float(c), 6) for c in rgb), round(float(alpha), 6), bool(non_color))
    img = _NEUTRAL_IMAGES.get(key)
    if img is not None:
        try:
            img.name
            return img
        except ReferenceError:
            pass
    name = 'RuriNeutral_{0:.3f}_{1:.3f}_{2:.3f}_{3:.3f}_{4}'.format(
        key[0][0], key[0][1], key[0][2], key[1], 'nc' if non_color else 'srgb')
    img = _host().plugin_data(bpy.data.images.new(name, 1, 1, float_buffer=True, alpha=True))
    _set_colorspace(img, 'Non-Color' if non_color else 'sRGB')
    img.alpha_mode = 'CHANNEL_PACKED'
    # 平色用 generated_color(不写 pixels:写像素置 dirty 且不进 .blend);sRGB 图按显示空间反编码。
    img.generated_color = (
        key[0][0] if non_color else _linear_to_srgb(key[0][0]),
        key[0][1] if non_color else _linear_to_srgb(key[0][1]),
        key[0][2] if non_color else _linear_to_srgb(key[0][2]),
        key[1])
    img.update()
    img['ruri_placeholder'] = 1
    _NEUTRAL_IMAGES[key] = img
    return img


# ==================== 着色器常量块预算 ====================

# 宿主编材质着色器时,每个节点的未接线值口(不可用的口、Math/VectorMath 没读的操作数口也算)各占 node_tree
# 常量块里一个 uniform;整块超过显卡的 UBO 上限宿主就不建这块 UBO(gpu_uniform_buffer.cc),着色器照编、
# 常量全读 0 —— 材质整片黑且零报错。上限 Python 查不到,取 N 卡 Vulkan 的 maxUniformBufferRange。
NODE_TREE_UNIFORM_LIMIT = 65536
_UNIFORM_SKIPPED = frozenset(('NodeFrame', 'NodeReroute'))
_UNIFORM_SCALARS = frozenset(('VALUE', 'INT', 'BOOLEAN'))
_UNIFORM_VECTORS = frozenset(('VECTOR', 'RGBA'))


def _mix_inputs(node):
    """Mix 只把当前数据类型用到的口编进着色器。"""
    if node.data_type == 'FLOAT':
        used = (0, 2, 3)
    elif node.data_type == 'VECTOR':
        used = (0 if node.factor_mode == 'UNIFORM' else 1, 4, 5)
    else:
        used = (0, 6, 7)
    return [node.inputs[index] for index in used]


def _uniform_slots(tree):
    """内联后平树的 (标量口数, 向量口数)。"""
    scalars = 0
    vectors = 0
    for node in tree.nodes:
        if node.bl_idname in _UNIFORM_SKIPPED:
            continue
        sockets = _mix_inputs(node) if node.bl_idname == 'ShaderNodeMix' else node.inputs
        for socket in sockets:
            if socket.is_linked or socket.hide_value:
                continue
            if socket.type in _UNIFORM_SCALARS:
                scalars += 1
            elif socket.type in _UNIFORM_VECTORS:
                vectors += 1
    return scalars, vectors


def node_tree_uniform_bytes(tree):
    """一棵内联后平树的 node_tree 常量块字节数(std140:向量各占 16,标量先填向量的尾巴)。"""
    scalars, vectors = _uniform_slots(tree)
    return 16 * vectors + 4 * max(0, scalars - vectors)


def _zone_copy(group, zone_copies):
    """组的「每个 repeat zone 砍成一圈」的拷贝;组里(连同它嵌套的组)没有 zone = None。拷贝是插件数据,
    按源组的 session_uid 记在 zone_copies 里:一个组一个会话只看一遍、只拷一次。"""
    uid = group.session_uid
    if uid not in zone_copies:
        zoned = any(node.bl_idname == 'GeometryNodeRepeatInput'
                    or (node.bl_idname == 'ShaderNodeGroup' and node.node_tree is not None
                        and _zone_copy(node.node_tree, zone_copies) is not None)
                    for node in group.nodes)
        copy = _host().plugin_data(group.copy()) if zoned else None
        zone_copies[uid] = copy
        if copy is not None:
            _one_iteration(copy, zone_copies)
    return zone_copies[uid]


def _one_iteration(tree, zone_copies):
    """把树里每个 repeat zone 砍成一圈:这棵树上的直接改,带 zone 的组换成它砍过的拷贝(见 _zone_copy)。"""
    for node in tree.nodes:
        if node.bl_idname == 'GeometryNodeRepeatInput':
            iterations = node.inputs['Iterations']
            for link in list(iterations.links):
                tree.links.remove(link)
            iterations.default_value = 1
        elif node.bl_idname == 'ShaderNodeGroup' and node.node_tree is not None:
            copy = _zone_copy(node.node_tree, zone_copies)
            if copy is not None:
                node.node_tree = copy


def require_uniform_budget(mat, zone_copies, probes):
    """量宿主真正编的那一份:材质树经内联器展开组、折叠常量、Switch 剪枝之后的平树。宿主编 EEVEE 时 repeat zone
    保留成 GLSL 循环、体只编一份,Python 的内联器却把它整个展开 —— 所以量的是一份把每个 zone(材质树上的与组里的)
    都砍成一圈的拷贝(初态是常量的项展开一圈会多占几格,量得偏多,落在保守的一边)。量用的材质拷贝记进 probes,
    由调用方整批删:逐张删每张都要把整个文件的 ID 引用走一遍。"""
    probe = _host().plugin_data(mat.copy())
    probes.append(probe)
    _one_iteration(probe.node_tree, zone_copies)
    inlined = bpy.types.InlineShaderNodes.from_material(probe)
    size = node_tree_uniform_bytes(inlined.node_tree)
    del inlined
    if size > NODE_TREE_UNIFORM_LIMIT:
        raise RuntimeError('[Ruri] 材质 {0} 的着色器常量块约 {1} 字节,超过宿主 UBO 上限 {2}:宿主会不建这块常量,'
                           '所有常量读 0、整片黑。生成的图要瘦下来。'.format(mat.name, size, NODE_TREE_UNIFORM_LIMIT))
    return size


# ==================== 灯:全部光照能力共用一套原生灯模板 ====================

# 源管线逐灯参数的一个向量在灯上的属性名(按结果叶取名):宿主经 light_parameter_attributes() 取同一份去写。
LIGHT_PARAMETER_ATTRIBUTE = 'ruri_light_{0}'
# 渲染输出一个像素的世界尺寸 = x + y × 视深(x 正交项、y 透视项),场景上的视层属性:宿主按渲染相机与输出分辨率
# 经 render_footprint_attributes() 取同一份名字去写,换镜头或分辨率只改值、不重接。
RENDER_FOOTPRINT_ATTRIBUTE = 'ruri_render_pixel_footprint'
# URP 给附加光的距离平方垫底值(Lighting.hlsl 的 max(dot(lightVector, lightVector), HALF_MIN))。
HALF_MIN = 6.103515625e-05
NATIVE_LIGHT_LABEL = 'RuriNativeLight'
# 终色之外多发的那一份:同一条终色,但附加光循环没跑过(生成期 LightTaint.NoLoopFinal)。
NO_LOOP_FINAL = '__color_noloop'
# 终色之外多发的另一份:同一条终色,但一盏灯都没有(生成期 LightTaint.UnlitFinal);终色不读灯的入口不发它。
UNLIT_FINAL = '__color_unlit'
# 入口交给表面光泽瓣的那一份(与终色一样住在光循环里,只在主光那一圈进账);没有这个出口的入口只有漫反射一瓣。
GLOSSY_FINAL = 'ret_glossyLight'

# 基座三列的组输入名 —— 与物化脚本 RIG_BASIS_SOCKETS 同一个命名域(那边建组,这边接线)。
RIG_BASIS_SOCKETS = ('_RuriRigBasis0', '_RuriRigBasis1', '_RuriRigBasis2')
RIG_ATTR_LABEL = 'RuriRigBasisAttr'
# 基座三列写在**对象**自定义属性上(逐对象 UBO,不弄脏材质树);Attribute 节点按 ["名"] 读。
RIG_OBJECT_PROP = 'ruri_face_basis'
# {对象名: (骨架名, 骨名)} —— push_rig_basis 的工作单,由 rig_apply 在接线时登记。
# 它是**进程态**:重开文件就空了,而 .blend 里只留着上次写下的属性值 ⇒ 不重建就等于
# 基座冻结在存盘那一刻,而且完全静默。所以 load_post 把 RIG_SCANNED 打回 False,
# 下一次 push 现扫一次重建(扫一次,不是每帧扫)。
RIG_DRIVEN = {}
RIG_SCANNED = [False]


# ==================== 能力兑现(身份 → 建图器 的唯一表) ====================

def _find_native_node(g, type_name, attribute_name=None):
    for node in g.t.nodes:
        if node.bl_idname != type_name or node.label != NATIVE_LIGHT_LABEL:
            continue
        if attribute_name is not None and node.attribute_name != attribute_name:
            continue
        return node
    node = g._nd(type_name)
    node.label = NATIVE_LIGHT_LABEL
    if attribute_name is not None:
        node.attribute_type = 'LIGHT'
        node.attribute_name = attribute_name
    return node


def _native_light(g):
    """当前光循环这一圈的灯 —— **全部光照能力共用的唯一模板**(Blender 世界系),各能力的答案都是它的视图:
    零驱动器、零灯表、零探针、零脚本写入,挪灯/转灯/换色由宿主自己的光循环当场带进来。这里交与位置无关的那几样:
    灯心与颜色(Light Info)、主光身份(原生 is_sun:方向光走主光链,点光/聚光/面光走附加光链)。颜色乘进源管线灯表
    按镜头距离给的那一份淡出(_camera_fade)。
    灯节点一进图,codegen 就把整棵材质树包进光循环,按灯逐圈求值 —— 主光着色读这一圈的方向,所以也逐圈求值,
    非方向光那几圈在出口与「循环没跑」的那份相减抵掉(见 _per_light_share)。
    只许在兑现面的标记区里调(能力建图器 / 闭包):灯源节点与换算链都随兑现面一起回收重建,
    树里任何时刻只有一条活链;同一次建图内按标签认领,不重复建源节点。"""
    hit = g._cse.get(('native_light',))
    if hit is not None:
        return hit
    info = _find_native_node(g, 'ShaderNodeLightInfo')
    position = info.outputs['Position']
    # Light Info.Power = 辐射强度/π;内核吃的是 color × intensity ⇒ 乘回 π。
    scale = g.math('MULTIPLY', g.math('MULTIPLY', info.outputs['Power'], math.pi), _camera_fade(g, position))
    answer = {
        'position': position,
        'color': g.vmath('SCALE', info.outputs['Color'], s=scale),
        'is_main': _light_builtin(g, 'is_sun'),
    }
    g._cse[('native_light',)] = answer
    return answer


def _light_builtin(g, name):
    """这一圈灯的原生属性(Attribute LIGHT 的内建名:is_sun / is_spot / cutoff_distance),读 Alpha 口:内建值四个分量
    同值,Alpha 原样、Fac 是三分量的平均。"""
    return _find_native_node(g, 'ShaderNodeAttribute', name).outputs['Alpha']


def _camera_fade(g, light_position):
    """源管线的灯表按镜头距离给这盏灯的那一份淡出,不画的灯为 0:saturate(1 + a - d²k) · saturate(1 + d²m - b),
    d = 相机到灯心,(a, k, b, m) 是灯上宿主照关卡陈述写下的 LIGHT_FADE_PROPERTY(没有这一格的灯读全零 = 不淡出)。
    相机位置 = 着色点 + 视向 × 视距,与灯无关,光循环外只算一次;淡出只随相机与灯心,视口里转镜头当场跟上。"""
    fade = _find_native_node(g, 'ShaderNodeAttribute', _host().LIGHT_FADE_PROPERTY)
    a, k, b = g.sep(fade.outputs['Vector'])
    m = fade.outputs['Alpha']
    view = g._cse.get(('camera_position',))
    if view is None:
        distance = g._nd('ShaderNodeCameraData').outputs['View Distance']
        view = g.vmath('MULTIPLY_ADD', g.geo().outputs['Incoming'], g.bc(distance), g.geo().outputs['Position'])
        g._cse[('camera_position',)] = view
    offset = g.vmath('SUBTRACT', view, light_position)
    distance2 = g.vmath('DOT_PRODUCT', offset, offset)
    far = g.math('MULTIPLY_ADD', distance2, g.math('MULTIPLY', k, -1.0), g.math('ADD', a, 1.0), clamp=True)
    near = g.math('MULTIPLY_ADD', distance2, m, g.math('SUBTRACT', 1.0, b), clamp=True)
    return g.math('MULTIPLY', far, near)


def _light_evaluation(g, position):
    """这一圈灯在一个位置上的原生 Light Evaluation:位置缺席 = 着色点(共享那一个原生节点);询问给了位置
    (真源的附加光按沿法线外推一点的位置算)就在那里单建一个,同一位置只建一个。"""
    if position is None:
        return _find_native_node(g, 'ShaderNodeLightEvaluation')
    at = g.u2b(position)
    key = ('light_evaluation', g._ck(at))
    node = g._cse.get(key)
    if node is None:
        node = g._nd('ShaderNodeLightEvaluation')
        g._set(node.inputs['Position'], at)
        g._cse[key] = node
    return node


def _cone_ramp(g, position):
    """聚光锥在这个位置上的线性斜坡 t = saturate((cos θ - cos 外角/2) / (cos 内角/2 - cos 外角/2)),点光与方向光恒 1。
    原生 Mask = 截止窗口 × smoothstep(t),两样乘在一起;同一条光线上离灯心 1/256 截止距离的那一点截止窗口恰为 1
    ((1/256)⁴ 在单精度里被 1 吞掉)而方向不变,那里的 Mask 就是 smoothstep(t) 本身。反解 smoothstep:
    t = ½ - sin(asin(1 - 2s) / 3)。"""
    evaluation = _light_evaluation(g, position)
    key = ('cone_ramp', evaluation.as_pointer())
    hit = g._cse.get(key)
    if hit is not None:
        return hit
    near = g._nd('ShaderNodeLightEvaluation')
    step = g.math('MULTIPLY', _light_builtin(g, 'cutoff_distance'), 1.0 / 256.0)
    g._set(near.inputs['Position'], g.vmath('SUBTRACT', _native_light(g)['position'],
                                            g.vmath('SCALE', evaluation.outputs['Direction'], s=step)))
    arc = g.math('ARCSINE', g.math('MULTIPLY_ADD', near.outputs['Mask'], -2.0, 1.0))
    ramp = g.math('SUBTRACT', 0.5, g.math('SINE', g.math('DIVIDE', arc, 3.0)))
    g._cse[key] = ramp
    return ramp


def _urp_attenuation(g, position):
    """URP 附加光的衰减:截止窗口 × 锥斜坡² / max(d², HALF_MIN)(Lighting.hlsl 的 DistanceAttenuation × AngleAttenuation)。
    原生 Mask = 窗口 × smoothstep(t) = 窗口 × t²(3 - 2t),除以 (3 - 2t) 就是窗口 × t²;方向光 Mask、t、Distance 恒 1,答 1。"""
    evaluation = _light_evaluation(g, position)
    distance = evaluation.outputs['Distance']
    return g.math('DIVIDE', evaluation.outputs['Mask'],
                  g.math('MULTIPLY', g.math('MULTIPLY_ADD', _cone_ramp(g, position), -2.0, 3.0),
                         g.math('MAXIMUM', g.math('MULTIPLY', distance, distance), HALF_MIN)))


def _light_axes(g):
    """这一圈灯的三根轴(Blender 世界系):right = 灯的 +X、up = +Y、forward = -Z(Blender 的灯沿自己的 -Z 照)。
    原生灯节点给不出灯的朝向:宿主在关卡陈述的灯上导入时烙下 LIGHT_FRAME_PROPERTY —— 世界旋转四元数的虚部
    (x, y, z),实部取非负、按单位长还原 w = sqrt(1 - |xyz|²) —— 与剔除盒、cookie 矩形同属导入时的静态内容,
    导入后再转这盏灯它不跟。没烙的灯读全零 = 单位旋转。读这三根轴的只有源管线的管光、雾聚光与 cookie,
    三者都只在烙了逐灯参数的灯上成立。"""
    hit = g._cse.get(('light_axes',))
    if hit is not None:
        return hit
    rotation = _find_native_node(g, 'ShaderNodeAttribute', _host().LIGHT_FRAME_PROPERTY).outputs['Vector']
    x, y, z = g.sep(rotation)
    w = g.math('SQRT', g.math('MAXIMUM', g.math('SUBTRACT', 1.0, g.vmath('DOT_PRODUCT', rotation, rotation)), 0.0))
    x2, y2, z2 = (g.math('ADD', component, component) for component in (x, y, z))
    xx, yy, zz = g.math('MULTIPLY', x, x2), g.math('MULTIPLY', y, y2), g.math('MULTIPLY', z, z2)
    xy, xz, yz = g.math('MULTIPLY', x, y2), g.math('MULTIPLY', x, z2), g.math('MULTIPLY', y, z2)
    wx, wy, wz = g.math('MULTIPLY', w, x2), g.math('MULTIPLY', w, y2), g.math('MULTIPLY', w, z2)
    right = g.comb(g.math('SUBTRACT', 1.0, g.math('ADD', yy, zz)), g.math('ADD', xy, wz), g.math('SUBTRACT', xz, wy))
    up = g.comb(g.math('SUBTRACT', xy, wz), g.math('SUBTRACT', 1.0, g.math('ADD', xx, zz)), g.math('ADD', yz, wx))
    forward = g.comb(g.math('MULTIPLY', g.math('ADD', xz, wy), -1.0), g.math('SUBTRACT', wx, yz),
                     g.math('SUBTRACT', g.math('ADD', xx, yy), 1.0))
    axes = (right, up, forward)
    g._cse[('light_axes',)] = axes
    return axes


def _world_background(scene):
    world = getattr(scene, 'world', None)
    if world is None or not world.use_nodes or world.node_tree is None:
        return None
    for nd in world.node_tree.nodes:
        if nd.type == 'BACKGROUND':
            return nd
    return None


def _world_sample(g, direction, spread):
    """沿方向对当前世界环境取一次值(环境图复用其 Mapping;平色世界给常量,精确非近似)。"""
    bg = _world_background(bpy.context.scene)
    if bg is None:
        return None
    strength = float(bg.inputs['Strength'].default_value)
    color_in = bg.inputs['Color']
    src = color_in.links[0].from_node if color_in.is_linked else None
    if src is not None and src.type == 'TEX_ENVIRONMENT' and src.image is not None:
        d = g.u2b(direction)
        if src.inputs['Vector'].is_linked:
            up = src.inputs['Vector'].links[0].from_node
            if up.type == 'MAPPING':
                md = g._nd('ShaderNodeMapping')
                md.vector_type = up.vector_type
                for key in ('Location', 'Rotation', 'Scale'):
                    md.inputs[key].default_value = up.inputs[key].default_value[:]
                g._set(md.inputs['Vector'], d)
                d = md.outputs[0]
        color, _alpha = g.env_image(src.image, d, spread=spread)
        return g.vmath('SCALE', color, s=strength) if strength != 1.0 else color
    c = color_in.default_value
    return (float(c[0]) * strength, float(c[1]) * strength, float(c[2]) * strength)


def _cap_ambient_irradiance(g, query, ctx):
    """环境辐照 = 世界环境本身。询问要的是**不含任何灯**的环境光(真源的球谐)。

    EEVEE 没有一条节点能答「不含灯」:Shader to RGB 走的是 forward_lighting_eval,每一盏灯的直接光
    都在里面;光循环把 Shader to RGB 的上游整段提前到循环之前求值,循环里逐灯算出的量要到循环结束才
    以闭包形式汇总,够不回那次求值;逐灯的漫反射系数(power_factor)同时乘在闭包求值与 Light
    Accumulation 上,灯那一侧也没有只关掉闭包那一份的开关。所以减掉主光那一份之后,附加光的直接光
    必然留在环境项里 —— 世界环境是宿主唯一给得出的无灯答案。"""
    _ = ctx
    normal = query.get('normal')
    if normal is None:
        return None
    answer = _world_sample(g, normal, spread=1.0)
    return None if answer is None else {'': answer}


def _cap_specular_radiance(g, query, ctx):
    _ = ctx
    direction = query.get('direction')
    if direction is None:
        return None
    roughness = query.get('roughness')
    spread = None if roughness is None else g.math(
        'MINIMUM', g.math('MULTIPLY', roughness, roughness), 1.0)
    answer = _world_sample(g, direction, spread=spread)
    return None if answer is None else {'': answer}


def _cap_main_light(g, query, ctx):
    """主方向光兑现:这一圈灯的原生方向(Light Evaluation 在着色点给的朝灯方向)与颜色,阴影(ShadowAttenuation)
    同是这一圈的原生量。哪一圈算主光由出口的身份门裁决(原生 is_sun):别的圈按别的灯算出的主光那一份在出口与
    「循环没跑」的那份相减抵掉,只有方向光那一圈进账。距离衰减与阴影衰减恒 1 是这条能力自己的语义(URP 的
    GetMainLight:距离衰减是 unity_LightData.z,没被剔除就是 1;无参形不带阴影),不是降级。"""
    _ = (query, ctx)
    return {
        'direction': g.b2u(_light_evaluation(g, None).outputs['Direction']),
        'color': _native_light(g)['color'],
        'distanceAttenuation': 1.0,
        'shadowAttenuation': 1.0,
        'layerMask': 1.0,
    }


def _shadow_raycast(g, position):
    """这一圈灯的原生 Shadow Raycast。询问带位置就在那个位置求值(真源把采样点沿光向推过一段时,推过的位置
    就是询问),同一个位置只建一个节点 —— 主光与附加光的阴影问的常是同一点;不带位置 = 着色点本身,用共享的
    原生节点(真源的 shadowCoord 是管线私产,不参与询问)。"""
    if position is None:
        return _find_native_node(g, 'ShaderNodeShadowRaycast')
    at = g.u2b(position)
    key = ('shadow_raycast', g._ck(at))
    raycast = g._cse.get(key)
    if raycast is None:
        raycast = g._nd('ShaderNodeShadowRaycast')
        g._set(raycast.inputs['Position'], at)
        g._cse[key] = raycast
    return raycast


def _cap_shadow_attenuation(g, query, ctx):
    """这一圈灯的阴影衰减:原生 Shadow Raycast —— 虚拟阴影图 + 光追,逐灯、带柔度。
    主光的询问在出口按主光身份门进账,答的就是方向光那一圈;附加光的询问住在附加光循环里
    (URP 的 AdditionalLightRealtimeShadow),每一圈答的就是那一圈的灯。"""
    _ = ctx
    shadow, _g, _b = g.sep(_shadow_raycast(g, query.get('position')).outputs['Color'])
    return {'': shadow}


def _cap_screen_space_occlusion(g, query, ctx):
    """屏幕空间遮蔽:宿主自己的环境光遮蔽节点 —— EEVEE 在它的深度层级里做地平线扫描。
    作用半径与末端幂次是询问给的管线那一趟的形状;询问没给半径就答不出那一趟,停在缺席值上。
    询问给了屏幕半径上限(像素)时,作用半径不超过这一像素深度上那么多个输出像素:一个输出像素的世界尺寸 = 正交项 +
    透视项 × 视深,两项在场景的 RENDER_FOOTPRINT_ATTRIBUTE 上。法线缺席 = 着色点自己的法线。"""
    _ = ctx
    radius = query.get('radius')
    if radius is None:
        return None
    pixel_radius = query.get('pixel_radius')
    if pixel_radius is not None:
        orthographic, perspective, _unused = g.sep(g.layer_attr(RENDER_FOOTPRINT_ATTRIBUTE).outputs['Vector'])
        depth = g._nd('ShaderNodeCameraData').outputs['View Z Depth']
        footprint = g.math('ADD', orthographic, g.math('MULTIPLY', perspective, depth))
        radius = g.math('MINIMUM', radius, g.math('MULTIPLY', pixel_radius, footprint))
    occlusion = g._nd('ShaderNodeAmbientOcclusion')
    g._set(occlusion.inputs['Distance'], radius)
    normal = query.get('normal')
    if normal is not None:
        g._set(occlusion.inputs['Normal'], g.u2b(normal))
    answer = occlusion.outputs['AO']
    power = query.get('power')
    if power is not None:
        answer = g.math('POWER', answer, power)
    return {'': answer}


def _cap_additional_light_count(g, query, ctx):
    """附加光盏数:恒 1。宿主的光循环已经在逐灯转圈,内核的附加光循环因此只需要
    在**每一圈**里跑一次体 —— 那一圈的灯就是这一盏。盏数不接灯节点,循环结构才与灯无关。"""
    _ = (g, query, ctx)
    return {'': 1.0}


def _cap_punctual_light(g, query, ctx):
    """第 index 盏点光/聚光本身:宿主光循环这一圈的灯,index 恒 0(盏数恒 1,见 _cap_additional_light_count)。
    灯心、颜色、范围(截止距离)、锥斜坡(在询问给的位置上)与是不是聚光全是原生量,三根轴是导入时烙下的朝向
    (见 _light_axes);源内核拿它们按自己的衰减窗口、锥角平方、管光与 cookie 去算。镜面倍率答 1,与 URP 宿主同答:
    原生光循环读不到灯的 Specular 滑杆,它只乘在 Light Accumulation 的光泽口上。方向光那一圈颜色乘零 —— 它不是点光,
    贡献走主光链。"""
    _ = ctx
    light = _native_light(g)
    right, up, forward = _light_axes(g)
    return {
        'position': g.b2u(light['position'], point=True),
        'color': g.vmath('SCALE', light['color'], s=g.math('SUBTRACT', 1.0, _light_builtin(g, 'is_sun'))),
        'range': _light_builtin(g, 'cutoff_distance'),
        'cone': _cone_ramp(g, query.get('position')),
        'spot': _light_builtin(g, 'is_spot'),
        'right': g.b2u(right),
        'up': g.b2u(up),
        'forward': g.b2u(forward),
        'specular': 1.0,
    }


def _cap_source_light_parameters(g, query, ctx):
    """第 index 盏灯带着的源管线逐灯参数:宿主光循环这一圈的灯上、宿主照关卡陈述写下的那几个向量属性,
    index 恒 0。每个向量是 LIGHT 型属性 LIGHT_PARAMETER_ATTRIBUTE.format(结果叶),宿主从 light_parameter_attributes()
    取同一份名字去写;全零的向量宿主不写,读回来一样是全零(结构体按「全零 = 宿主的一盏普通灯」编码)。"""
    _ = query
    results = ctx['results']
    answer = {}
    for leaf, vector in results.items():
        if not vector:
            continue
        node = _find_native_node(g, 'ShaderNodeAttribute', LIGHT_PARAMETER_ATTRIBUTE.format(leaf))
        answer[leaf] = node.outputs['Vector']
        if leaf + '_w' in results:
            answer[leaf + '_w'] = node.outputs['Alpha']
    return answer


def _cap_additional_light(g, query, ctx):
    """第 index 盏附加光:就是宿主光循环这一圈的灯,index 恒 0;方向与衰减在询问给的位置上求。
    主光那一圈把距离衰减乘零 —— 它的贡献走主光链,绝不能在附加光项里再进账一遍。
    阴影衰减恒 1:附加光不投影(内核无参附加光的语义)。"""
    _ = ctx
    position = query.get('position')
    light = _native_light(g)
    return {
        'direction': g.b2u(_light_evaluation(g, position).outputs['Direction']),
        'color': light['color'],
        'distanceAttenuation': g.math('MULTIPLY', _urp_attenuation(g, position),
                                      g.math('SUBTRACT', 1.0, light['is_main'])),
        'shadowAttenuation': 1.0,
        'layerMask': 1.0,
    }


def _cap_additional_light_kind(g, query, ctx):
    """第 index 盏附加光的灯型(0 方向光 / 1 点光,URP 位置记录 w 的同一编码):这一圈灯的原生 is_sun 取反。"""
    _ = (query, ctx)
    return {'': g.math('SUBTRACT', 1.0, _light_builtin(g, 'is_sun'))}


# 询问身后不透明面的部件,每个顶点离它立着的那张面有多高:点属性 (高度 米·世界尺度, 1, 0),宿主导入时量好
# (_prepare_surface_height),兑现面(_cap_opaque_distance_behind)读它。第二格恒 1 是「量过」的记号:属性缺席
# 读到的全零因此是能力的缺席语义「身后无物」,而不是「面就在脚下」—— 后者会让询问它的部件整片消失。
# 高度按原值存、不存倒数:片元上的值是三个顶点的线性插值,毛根贴着面、高度近零,倒数一插值整片都被当成贴在面上。
SURFACE_HEIGHT_ATTRIBUTE = 'ruri_surface_height'
# 混合材质问身后那张面时原生 Raycast 的射程(米):远过任何关卡,光线停在第一张不透明面上;精度不随射程变(实测 1e5 与
# 200 的命中距离逐位相同),没打到 = 视野里身后无物。
OPAQUE_BEHIND_REACH = 1.0e5


def _cap_opaque_distance_behind(g, query, ctx):
    """身后的不透明面。材质图读不到深度缓冲,答案从这一点立在哪张面上来:宿主导入时给询问它的
    部件量好每个顶点离它立着的那张面的高度,着色点把那张面当成以询问给的法线为法向、在它下方这个高度处的
    局部平面 —— 视线沿 -Incoming 穿过去要走 高度 / (Incoming · 法线),线性深度再乘 (-Incoming · 相机前向)。
    视线背着那张面(Incoming · 法线 ≤ 0)时它不在身后,没量过的网格(记号读 0)不知道身后有什么,
    两者都答能力自声明的缺席值。

    询问给的法线是插值法线的原样(见 G.surface_normal),这一点立着的那张面本来就不随毛片被看到的是哪一面
    翻转。相机前向在宿主的相机空间里是 +Z:Vector Transform 从相机空间出发时先把 Z 取反
    (node_shader_vector_transform 的 invert_z,照 Cycles 的相机系)。

    混合(BLENDED)的材质不进 EEVEE 的深度缓冲:从询问的那一点沿相机穿过它的视线往里投一条原生 Raycast,
    打到的就是深度缓冲在这一像素存的那张不透明面(实测到毫米),深度差 = 命中距离 × 视线在相机前向上的分量;
    没打到 = 身后无物,答能力缺席值。写深度的材质把自己身后挡住了,屏幕空间的光线看不见,仍按立着的那张面答。"""
    material = ctx.get('material')
    if material is not None and material.surface_render_method == 'BLENDED':
        position = query.get('position')
        if position is None:
            return None
        origin = g.u2b(position)
        camera = g.vtrans((0.0, 0.0, 0.0), 'CAMERA', 'WORLD', 'POINT')
        ray = g.vmath('NORMALIZE', g.vmath('SUBTRACT', origin, camera))
        raycast = g._nd('ShaderNodeRaycast')
        g._set(raycast.inputs['Position'], origin)
        g._set(raycast.inputs['Direction'], ray)
        g._set(raycast.inputs['Length'], OPAQUE_BEHIND_REACH)
        forward = g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD')
        deeper = g.math('MULTIPLY', raycast.outputs['Hit Distance'], g.vmath('DOT_PRODUCT', ray, forward))
        nothing = float(ctx['absent'][''][0])
        return {'': g.choosef(raycast.outputs['Is Hit'], nothing, deeper)}
    normal = query.get('surface_normal')
    if normal is None:
        return None
    incoming = g.geo().outputs['Incoming']
    rise = g.vmath('DOT_PRODUCT', incoming, g.u2b(normal))
    forward = g.vtrans((0.0, 0.0, 1.0), 'CAMERA', 'WORLD')
    deepening = g.math('MULTIPLY', g.vmath('DOT_PRODUCT', incoming, forward), -1.0)
    height, measured, _unused = g.sep(g.attr(SURFACE_HEIGHT_ATTRIBUTE).outputs['Vector'])
    through = g.math('DIVIDE', g.math('MULTIPLY', height, deepening), g.math('MAXIMUM', rise, 1e-6))
    behind = g.math('MULTIPLY', g.math('GREATER_THAN', rise, 0.0), g.math('GREATER_THAN', measured, 0.5))
    nothing = float(ctx['absent'][''][0])
    return {'': g.choosef(behind, nothing, through)}


def _prepare_surface_height(obj, asking, ground):
    """_cap_opaque_distance_behind 的宿主数据:asking 槽上每个顶点离它立着的那张面有多高,写成点属性
    SURFACE_HEIGHT_ATTRIBUTE = (高度, 1, 0)(米)。

    询问它的几何是立在面上的卡:每张 asking 面上离 ground 那些面最低的顶点是这张面的根,过根、以顶点法线为法向的平面
    就是它立着的那张面 —— 高度 = (顶点 − 根) · 顶点法线 + 根离面的有符号距离;一个顶点落在几张面上时取根最低的那一张。
    距离带符号(沿最近那张面的法线,扎进面里为负):毛根常常扎在绒面底下半毫米到一毫米多,当成正的就把整片毛
    抬高了两倍那么多。符号只在最近那张面与顶点法线同向时才算数 —— 那才是它立着的面;最近的是背着它的面
    (绒球下沿挨着的编绳)时符号说的是别的部件的里外,取无符号距离,这样的顶点也不当根:垂下来的毛梢挨着
    编绳比毛根挨着绒面还近,拿它当根,整片毛就落到根平面底下两三厘米。
    根按单张面找,不按连成一块的整片找:毛片常常连成绕着绒球的一整圈,一圈只有一个根,圈另一边的顶点全落到根平面
    底下。也不取每个顶点各自离 ground 的最近距离:毛片挨着别的部件时(绒球下沿贴着编绳),最近的是那个部件,
    不是毛根底下的绒面,整片毛就被当成贴在面上。法线取网格的角法线,宿主插值给片元的就是它。ground 是本物体上
    本栈认领的其余着色表面,描边壳、投影代理这些不着色的不算 —— 它们在真源里不是毛根身后的那张面。

    量的是 rest 网格经物体矩阵落到世界的几何:蒙皮带着顶点连同这个值一起走,毛片与它立着的面一起形变,
    离面的高度不变,所以只在导入时量一次、逐帧零成本。其余顶点写全零(没量过 = 身后无物),没有部件读它。"""
    import numpy as _np
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    mesh = obj.data
    count = len(mesh.vertices)
    local = _np.empty(count * 3, dtype=_np.float64)
    mesh.vertices.foreach_get('co', local)
    matrix = _np.array(obj.matrix_world, dtype=_np.float64)
    world = local.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    polygon_count = len(mesh.polygons)
    material_index = _np.empty(polygon_count, dtype=_np.int32)
    mesh.polygons.foreach_get('material_index', material_index)
    loop_start = _np.empty(polygon_count, dtype=_np.int32)
    mesh.polygons.foreach_get('loop_start', loop_start)
    loop_total = _np.empty(polygon_count, dtype=_np.int32)
    mesh.polygons.foreach_get('loop_total', loop_total)
    corner_vertex = _np.empty(len(mesh.loops), dtype=_np.int32)
    mesh.loops.foreach_get('vertex_index', corner_vertex)
    heights = _np.zeros((count, 3), dtype=_np.float32)
    grounded = _np.isin(material_index, list(ground))
    standing = _np.isin(material_index, list(asking))
    if grounded.any() and standing.any():
        corner_normal = _np.empty(len(mesh.loops) * 3, dtype=_np.float64)
        mesh.corner_normals.foreach_get('vector', corner_normal)
        corner_normal = corner_normal.reshape(-1, 3) @ _np.linalg.inv(matrix[:3, :3])
        faces = [corner_vertex[start:start + total].tolist()
                 for start, total in zip(loop_start[grounded], loop_total[grounded])]
        tree = BVHTree.FromPolygons(world.tolist(), faces)
        normals = _np.zeros((count, 3), dtype=_np.float64)
        spans = list(zip(loop_start[standing].tolist(), loop_total[standing].tolist()))
        for start, total in spans:
            _np.add.at(normals, corner_vertex[start:start + total], corner_normal[start:start + total])
        lengths = _np.linalg.norm(normals, axis=1)
        normals /= _np.maximum(lengths, 1e-30)[:, None]
        clearance = {}
        grounded_on = set()
        for vertex in _np.unique(_np.concatenate([corner_vertex[start:start + total]
                                                   for start, total in spans])).tolist():
            point = Vector(world[vertex])
            location, face_normal, _index, distance = tree.find_nearest(point)
            if location is None:
                continue
            if face_normal.dot(Vector(normals[vertex])) > 0.0:
                clearance[vertex] = (point - location).dot(face_normal)
                grounded_on.add(vertex)
            else:
                clearance[vertex] = distance
        anchor = {}
        for start, total in spans:
            corners = corner_vertex[start:start + total].tolist()
            known = [vertex for vertex in corners if vertex in clearance]
            if not known:
                continue
            rooted = [vertex for vertex in known if vertex in grounded_on] or known
            root = min(rooted, key=clearance.get)
            for vertex in corners:
                held = anchor.get(vertex)
                if held is None or clearance[root] < clearance[held]:
                    anchor[vertex] = root
        for vertex, root in anchor.items():
            height = float(_np.dot(world[vertex] - world[root], normals[vertex])) + clearance[root]
            heights[vertex] = (height, 1.0, 0.0)
    attribute = mesh.attributes.get(SURFACE_HEIGHT_ATTRIBUTE)
    if attribute is not None and (attribute.domain != 'POINT' or attribute.data_type != 'FLOAT_VECTOR'):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new(SURFACE_HEIGHT_ATTRIBUTE, 'FLOAT_VECTOR', 'POINT')
    attribute.data.foreach_set('vector', heights.ravel())


# 能力身份 → 建图器(EEVEE 一条腿)。表里查不到 = 宿主没有原生等价物:什么都不接,
# socket 停在能力自声明缺席值上并大声记账 —— 绝不静默落中性数。
CAP_BUILDERS = {
    'AmbientIrradiance': _cap_ambient_irradiance,
    'SpecularRadiance': _cap_specular_radiance,
    'MainLight': _cap_main_light,
    'ShadowAttenuation': _cap_shadow_attenuation,
    'AdditionalLightCount': _cap_additional_light_count,
    'AdditionalLight': _cap_additional_light,
    'PunctualLight': _cap_punctual_light,
    'SourceLightParameters': _cap_source_light_parameters,
    'AdditionalLightKind': _cap_additional_light_kind,
    'OpaqueDistanceBehind': _cap_opaque_distance_behind,
    'ScreenSpaceOcclusion': _cap_screen_space_occlusion,
}

# 能力身份 → 逐物体的宿主数据准备(兑现面读的网格属性由它写):(物体, 询问它的槽, 本物体其余着色槽)。
# 只在导入那一刻跑(apply_vertex_stage),量的是网格自己的几何事实。
CAP_PREPARERS = {
    'OpaqueDistanceBehind': _prepare_surface_height,
}

# ==================== 栈(每个 .blend 产物一个;全部知识来自内嵌清单) ====================

def _mixed(a):
    return {k: list(v) for k, v in dict(a or {}).items()}


# 随绑定真图走的表列:显式 LOD 槽的纹素域尺寸 __size<槽> = (w, h, 1, 0),以及引擎按绑定纹理
# 自动下发的 <槽>_TexelSize = (1/w, 1/h, w, h)(不是材质属性;宿主不填就停在 0)。
TEXEL_SIZE_SUFFIX = '_TexelSize'
SIZE_ROW_PREFIX = '__size'

# 组实例上参数口的类型 → 它在 inputs 平铺数组(foreach_get/foreach_set 的那一份)里占几个数。
PARAM_SOCKET_WIDTHS = {'VALUE': 1, 'VECTOR': 3}


def _image_row_slot(row_name):
    if row_name.startswith(SIZE_ROW_PREFIX):
        return row_name[len(SIZE_ROW_PREFIX):]
    if row_name.endswith(TEXEL_SIZE_SUFFIX):
        return row_name[:-len(TEXEL_SIZE_SUFFIX)]
    return None


def _image_row_value(row_name, image):
    width, height = float(image.size[0]), float(image.size[1])
    if row_name.startswith(SIZE_ROW_PREFIX):
        return [width, height, 1.0, 0.0]
    return [1.0 / width, 1.0 / height, width, height]


_LINKED = {}

# 一次 append 能把哪几类数据块一并带进来:模板组嵌套的组、组里图节点绑的图、几何组里指到的材质与物体。
_APPENDED_KINDS = ('node_groups', 'images', 'materials', 'textures', 'objects', 'collections', 'meshes', 'texts')

# 认得出、但没有表面可画的那一类(反壳描边的壳 / 只写模板的代理 / 面部阴影代理面片)。
# 与「不认领」是两码事:不认领会掉回宿主那张不透明的兜底材质,而这些代理面片就贴在角色身上。
NON_SHADING = object()

# Unity BlendMode 的引擎序:清单里真源 pass 的混合因子、材质属性里的混合值都按它记。
(BLEND_ZERO, BLEND_ONE, BLEND_DST_COLOR, BLEND_SRC_COLOR, BLEND_ONE_MINUS_DST_COLOR, BLEND_SRC_ALPHA,
 BLEND_ONE_MINUS_SRC_COLOR, BLEND_DST_ALPHA, BLEND_ONE_MINUS_DST_ALPHA, BLEND_SRC_ALPHA_SATURATE,
 BLEND_ONE_MINUS_SRC_ALPHA) = range(11)
# 原样兑现的两种:源 α 的 over 与不透明。乘法帧另走带色透射;其余按「源 × A + 帧 × T」拆。
OVER_BLENDS = ((BLEND_SRC_ALPHA, BLEND_ONE_MINUS_SRC_ALPHA), (BLEND_ONE, BLEND_ZERO))
MULTIPLY_BLENDS = ((BLEND_ZERO, BLEND_SRC_COLOR), (BLEND_DST_COLOR, BLEND_ZERO))


def _alive(block):
    # 缓存的是数据块引用,被释放后再碰是 ReferenceError 而不是 None —— 必须判活。
    if block is None:
        return False
    try:
        block.name
    except ReferenceError:
        return False
    return True


def _pointers():
    return {block.as_pointer() for kind in _APPENDED_KINDS for block in getattr(bpy.data, kind)}


def _plain(value):
    """ID 属性的值拷成纯 python(组 → dict、数组 → list),好写到另一个数据块上。"""
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    if hasattr(value, 'to_list'):
        return value.to_list()
    return value


def _linked_group(blend_path, stamp, wanted, name, adopt):
    """本栈的模板组:从产物 append 进本会话 —— 连同这一次带进来的每个数据块一律是插件数据(运行时,存盘不带,
    见模块头)。首次真用才取:register() 期 bpy.data 还是 _RestrictData。本模块这一生里取一次,开文件换了整个
    Main、或取过的组有哪张没了(复位场景清掉没人用的图链组)就再取一次。同名的别的数据块让出名字,新取的才叫得上
    这个名字 —— 按名寻址的地方(合成树找主入口)撞上别的就是两代混建;还有人用的那张先把用户换到新取的这一份上
    再删(场景树上装着的主入口照样挂着,删了它就是一个没有组的节点)。这一次带进来的数据块交给 adopt 认领一次。"""
    table = _LINKED.get(blend_path)
    if table is not None and all(_alive(group) for group in table.values()):
        got = table.get(name)
        if got is not None:
            return got
    requested = list(dict.fromkeys(wanted))
    displaced = {}
    for wanted_name in requested:
        taken = bpy.data.node_groups.get(wanted_name)
        if taken is not None and taken.library is None:
            if taken.users:
                taken.name = wanted_name + ' (displaced)'
                displaced[wanted_name] = taken
            else:
                bpy.data.node_groups.remove(taken)
    before = _pointers()
    # 🔴 `dst.node_groups = <list>` 之后 Blender **就地把那个列表的内容换成数据块**:名字先快照,
    # 给 Blender 一份独立的列表,按位次配对。
    with bpy.data.libraries.load(blend_path, link=False) as (source, target):
        absent = [n for n in requested if n not in source.node_groups]
        if absent:
            raise RuntimeError('[ruri-uber] 产物 {0} 缺模板组 {1}:请重新 codegen(无现场重建退路)'.format(
                blend_path, absent))
        target.node_groups = list(requested)
    loaded = list(target.node_groups)
    if len(loaded) != len(requested):
        raise RuntimeError('[ruri-uber] 产物 {0} append 回来 {1} 组,请求 {2} 组:配对不上,拒绝接线'.format(
            blend_path, len(loaded), len(requested)))
    plugin_data = _host().plugin_data
    appended = []
    for kind in _APPENDED_KINDS:
        for block in getattr(bpy.data, kind):
            if block.as_pointer() not in before:
                plugin_data(block)
                appended.append(block)
    table = {}
    for wanted_name, group in zip(requested, loaded):
        if group is None:
            raise RuntimeError('[ruri-uber] 产物 {0} 的模板组 {1} append 失败'.format(blend_path, wanted_name))
        stamped = group.get('ruri_stamp')
        if stamped != stamp:
            raise RuntimeError(
                '[ruri-uber] {0}: 组 {1} 的 stamp {2} ≠ 运行时 {3},产物不同批,请重新 codegen'.format(
                    blend_path, wanted_name, stamped, stamp))
        if group.name != wanted_name:
            raise RuntimeError('[ruri-uber] 产物 {0} 的模板组 {1} 取回来叫 {2}:名字被 link 进来的同名组占着'.format(
                blend_path, wanted_name, group.name))
        table[wanted_name] = group
    for wanted_name, old in displaced.items():
        old.user_remap(table[wanted_name])
        bpy.data.node_groups.remove(old)
    _LINKED[blend_path] = table
    adopt(appended)
    got = table.get(name)
    if got is None:
        raise RuntimeError('[ruri-uber] 模板组 {0} 不在产物 {1} 里'.format(name, blend_path))
    return got


class Stack:
    def __init__(self, folder, manifest):
        # 本栈的 .blend 与本运行时同批出货、同目录:名字来自清单,不猜也不扫。
        self.path = os.path.join(folder, manifest['blend'])
        self.group_names = manifest['group_names']
        self.m = manifest
        self._vtx_seen = {}
        self._applied_values = {}
        names = manifest['names']
        self.PANEL_KEY = names['panel_key']
        self.PANEL_TITLE = names['panel_title']
        self.INTERFACE = manifest.get('interface') or []
        self.PART_META = manifest.get('part_meta') or {}
        self.NON_SHADING = set(manifest.get('non_shading') or [])
        self.HOST_SHADOW_CASTERS = manifest['host_shadow_casters']
        # 游戏词汇 → 风格词汇。清单里记的是 风格名 → 游戏名(声明面朝着图的口),
        # 这里翻过来存:材质自述时报的是游戏名,要按它查。
        self.SOURCE_NAMES = {game: uniform for uniform, game
                             in (manifest.get('source_names') or {}).items()}
        self.GLOBALS = manifest.get('globals') or {}
        self.KNOWN_PARTS = set(manifest.get('known_parts') or [])
        self.DEFAULT_PART = manifest.get('default_part') or ''
        cull = manifest.get('cull') or {}
        self.CULL_PROPERTY = cull.get('property') or ''
        self.CULL_FIXED = float(cull.get('fixed', 2.0))
        self.CULL_TWO_SIDED = cull.get('two_sided')
        self.STAMP = manifest['stamp']
        self.TEMPLATE_MAT = names['template_mat']
        self.VTX_MODIFIER = names['vtx_modifier']
        self.VTX_TREE_PREFIX = names['vtx_tree_prefix']
        self.MATERIAL_NAME = names['material_name']
        self.ST_SLOT = names.get('st_slot', '_BaseMap')
        self.ST_NODE = names.get('st_node', 'RuriBaseMapST')
        self.VERTEX_PARTS = manifest.get('vertex_parts') or {}
        # 作者值住 sRGB 的属性名(见清单同名键)。快照恒是 .mat 里的原值,线性化只发生在
        # 「值 → uniform」那一步,与 Unity 上传时做的那次是同一件事。
        self.SRGB_PARAMS = set(manifest.get('srgb_params') or [])
        self.RIG = manifest.get('rig') or {'bone': '', 'attr': '', 'parts': []}
        self.host = manifest.get('host') or {}
        declare_world_basis(self.host.get('world_basis'))
        self.post = manifest.get('post')
        self.engine_global_sources = manifest.get('engine_global_sources') or {}
        self.object_attributes = manifest.get('object_attributes') or {}
        self.texel_sizes = manifest.get('texel_sizes') or {}
        self.level_images = manifest.get('level_images') or {}
        if manifest.get('kernel') != RUNTIME_KERNEL:
            raise RuntimeError('[ruri-uber] 栈 {0} 的内核 {1} ≠ 运行时 {2},请重新 codegen(禁兼容,无退路)'.format(
                self.PANEL_KEY, manifest.get('kernel'), RUNTIME_KERNEL))
        self._param_rows = {}
        self._param_slots = {}
        self._budgeted = set()
        self._zone_copies = {}
        # 整批照记录重编期间量常量块用的材质拷贝攒在这里,收尾与旧材质一起删(见 compile_all);None = 量完即删。
        self._probes = None

    def part(self, part):
        """part 的接线面:段、跨段、终点、割点、循环、参数行与 Switch 条件读到的参数。"""
        return self.m['parts'][part]

    # ---- 取模板面(产物即库;缺组响亮拒绝,零现场重建退路) ----

    def group(self, name):
        return _linked_group(self.path, self.STAMP, self.group_names, name, self._adopt_level_images)

    def _adopt_level_images(self, appended):
        """产物组里关卡图的取点绑的是物化时的占位图(一个槽一张):一次 user_remap 把它的全部用户换到宿主按关卡建的
        那一张上(全场共享,不随材质变),不逐节点写 —— 每写一个节点宿主就把整棵大组重算一遍。占位图随后删掉。"""
        placeholders = [block for block in appended if isinstance(block, bpy.types.Image)
                        and block.get('ruri_placeholder') and _slot_of(block.name) in self.level_images]
        for image in placeholders:
            image.user_remap(self._level_image(_slot_of(image.name)))
        if placeholders:
            bpy.data.batch_remove(placeholders)

    # ==================== 材质参数(材质 = 组输入上的常量) ====================

    def _param_index(self, part):
        """part 的参数行按名 → 行序(参数值矩阵按这个序排)。"""
        index = self._param_rows.get(part)
        if index is None:
            index = {row[0]: at for at, row in enumerate(self.part(part)['params'])}
            self._param_rows[part] = index
        return index

    def _param_matrix(self, part, floats, st, colors):
        """这张材质每一行参数的 uniform 值,按行序一行四分量:声明缺省,记录覆写,再补 Unity 上传时那一次
        sRGB→linear(缺省与覆写走同一条,两者都是作者值;alpha 不参与,颜色的第四分量从来不是色度)。"""
        import numpy as np
        merged = dict(floats or {})
        for prop, value in (st or {}).items():
            merged[prop + '_ST'] = value
        merged.update(colors or {})
        rows = self.part(part)['params']
        matrix = np.zeros((len(rows), 4), dtype=np.float64)
        for at, (name, kind, declared, declared_w) in enumerate(rows):
            vector = matrix[at]
            if kind == 'F':
                vector[0] = declared[0]
            else:
                vector[0:3] = declared
                vector[3] = declared_w
            value = merged.get(name)
            if value is not None:
                if kind == 'F':
                    try:
                        vector[0] = float(value)
                    except (TypeError, ValueError):
                        pass
                else:
                    spread = ([float(x) for x in value] + [0.0] * 4 if hasattr(value, '__len__')
                              else [float(value)] * 3 + [0.0])
                    vector[0:4] = spread[0:4]
            if name in self.SRGB_PARAMS:
                for channel in range(1 if kind == 'F' else 3):
                    vector[channel] = _srgb_to_linear(vector[channel])
        return matrix

    def _record_matrix(self, mat):
        return self._param_matrix(mat.get('ruri_uber_part', ''), dict(mat.get('ruri_uber_floats') or {}),
                                  _mixed(mat.get('ruri_uber_st')), _mixed(mat.get('ruri_uber_colors')))

    @staticmethod
    def _record_values(mat):
        """材质记录里的值行(作者值),可比形态:apply_params 落树时记一份,依赖图那一拍拿它判断记录是不是被别处改过。"""
        return {'floats': {k: _plain(v) for k, v in dict(mat.get('ruri_uber_floats') or {}).items()},
                'colors': _mixed(mat.get('ruri_uber_colors')),
                'st': _mixed(mat.get('ruri_uber_st'))}

    def _param_targets(self, node, part):
        """组实例的参数口在 inputs 平铺数组里的位置:(平铺长度, 目标下标, 值矩阵平铺下标)。按 (组树, part) 算一次;
        组树是本会话取来的插件数据,session_uid 在一个会话里不复用。"""
        import numpy as np
        group = node.node_tree
        key = (group.session_uid, part)
        targets = self._param_slots.get(key)
        if targets is not None:
            return targets
        index = self._param_index(part)
        kinds = {row[0]: row[1] for row in self.part(part)['params']}
        destinations = []
        sources = []
        width = 0
        for socket in node.inputs:
            size = PARAM_SOCKET_WIDTHS.get(socket.type)
            if size is None:
                raise RuntimeError('[ruri-uber] 组 {0} 的输入 {1} 是 {2} 口:参数一次写完只认数值口'.format(
                    group.name, socket.name, socket.type))
            name = socket.name
            lanes = ()
            if name in index:
                row = index[name]
                lanes = range(size)
            elif name.endswith('_w') and kinds.get(name[:-2]) == 'V4':
                row = index[name[:-2]]
                lanes = (3,)
            for offset, lane in enumerate(lanes):
                destinations.append(width + offset)
                sources.append(row * 4 + lane)
            width += size
        targets = (width, np.array(destinations, dtype=np.int64), np.array(sources, dtype=np.int64))
        self._param_slots[key] = targets
        return targets

    def apply_params(self, mat):
        """材质记录 → 它图里的全部参数口(各段与循环体的组实例、显式 LOD 采样的纹素域尺寸),值写成常量:宿主内联
        时只读参数的算式整块折掉,Switch 只留走到的那一支。导入、开文件重编、面板与关卡推值都走这一条;组实例的口
        一次写完(foreach_set 不逐口触发树更新)。写完量一次内联后的常量块(同一结构只量一次)。"""
        import numpy as np
        part = mat.get('ruri_uber_part', '')
        tree = mat.node_tree
        if tree is None or part not in self.m['parts']:
            return
        matrix = self._record_matrix(mat)
        values = matrix.ravel()
        index = self._param_index(part)
        for node in tree.nodes:
            if node.bl_idname == 'ShaderNodeGroup' and node.node_tree is not None:
                width, destinations, sources = self._param_targets(node, part)
                if not len(destinations):
                    continue
                flat = np.empty(width, dtype=np.float32)
                node.inputs.foreach_get('default_value', flat)
                flat[destinations] = values[sources]
                node.inputs.foreach_set('default_value', flat)
            elif (node.bl_idname == 'ShaderNodeCombineXYZ' and node.label.startswith(SIZE_ROW_PREFIX)
                  and node.label in index):
                extent = matrix[index[node.label]]
                for axis in range(3):
                    if node.inputs[axis].default_value != float(extent[axis]):
                        node.inputs[axis].default_value = float(extent[axis])
        tree.update_tag()
        mat.update_tag()
        self._applied_values[mat.session_uid] = self._record_values(mat)
        self._require_budget(mat, matrix)

    def _require_budget(self, mat, matrix=None):
        """常量块预算量的是内联后的着色器:参数是常量,只读参数的算式折掉、Switch 剪枝,余下的常量各占一格。
        内联出来的结构只取决于材质树(模板与兑现面)和 Switch 条件读到的参数取值 —— 同一结构只量一次。"""
        part = mat.get('ruri_uber_part', '')
        if part not in self.m['parts'] or mat.node_tree is None:
            return
        if matrix is None:
            matrix = self._record_matrix(mat)
        index = self._param_index(part)
        switches = tuple(tuple(float(x) for x in matrix[index[name]]) for name in self.part(part)['switch_params'])
        tree = mat.node_tree
        key = (part, len(tree.nodes), len(tree.links), switches)
        if key in self._budgeted:
            return
        probes = [] if self._probes is None else self._probes
        try:
            require_uniform_budget(mat, self._zone_copies, probes)
        finally:
            if self._probes is None:
                bpy.data.batch_remove(probes)
        self._budgeted.add(key)

    # ==================== 割点兑现 ====================

    @staticmethod
    def _size_row(slot):
        return SIZE_ROW_PREFIX + slot

    def _image_rows(self, part, images):
        """本 part 随绑定真图走的表列取值;没绑图的槽不给值(留在表列缺省)。"""
        rows = {}
        for row_name, _kind, _declared, _declared_w in self.part(part)['params']:
            slot = _image_row_slot(row_name)
            real = None if slot is None else (images or {}).get(slot)
            if real is not None and real.size[0] and real.size[1]:
                rows[row_name] = _image_row_value(row_name, real)
        return rows

    def _teximage(self, g, fetch, image, images, force_closest=False):
        nd = g._nd('ShaderNodeTexImage')
        nd.label = fetch['slot']
        nd.width = 260
        lender = _sampler_lender(fetch['extension'])
        if fetch['extension'] == OWN_SAMPLER or lender is not None:
            nd[OWN_SAMPLER_KEY] = lender if lender is not None else 1
            _apply_own_sampler(nd, _sampler_image(nd, image, images))
        else:
            nd.extension = fetch['extension']
            if fetch['point']:
                nd.interpolation = 'Closest'
        if force_closest:
            nd.interpolation = 'Closest'
        nd.image = image
        if image is not None:
            _set_colorspace(image, 'Non-Color' if fetch['non_color'] else 'sRGB')
            if fetch['non_color']:
                _fix_two_channel_layout(image)
        return nd

    def _texel_extent(self, g, part, fetch, image):
        """显式 LOD 采样的纹素域尺寸落在一枚标签为 __size<槽> 的 CombineXYZ 上,实例按这张材质绑的真图写(apply_params)。
        行按 part 查:同名槽跨 part 各有各的行。"""
        row_name = self._size_row(fetch['slot'])
        if row_name not in self._param_index(part):
            raise RuntimeError('[ruri-uber] part {0} 的显式 LOD 割点 {1} 没有纹素域尺寸行:产物与清单脱节'.format(
                part, fetch['slot']))
        extent = (float(image.size[0]), float(image.size[1]), 1.0)
        node = g._nd('ShaderNodeCombineXYZ')
        node.label = row_name
        for axis in range(3):
            node.inputs[axis].default_value = extent[axis]
        return node.outputs[0]

    def _sample(self, g, part, fetch, image, uv, images):
        """割点采样语义兑现:隐式形 = 单 TexImage(mip 交 EEVEE);显式 LOD 形 = 四角 Closest 手工双线性,
        纹素域尺寸随材质绑的真图写成常量(见 _texel_extent)。"""
        if image is None:
            return None, None, None
        if fetch['derivative_mip'] or not image.size[0] or not image.size[1]:
            nd = self._teximage(g, fetch, image, images)
            g._set(nd.inputs['Vector'], uv)
            return nd.outputs['Color'], nd.outputs['Alpha'], nd
        size = self._texel_extent(g, part, fetch, image)
        u, v, _w = g.sep(uv)
        extents = size[:2] if isinstance(size, tuple) else g.sep(size)[:2]
        axes = []
        for coordinate, extent in zip((u, v), extents):
            texel = g.math('MULTIPLY_ADD', coordinate, extent, -0.5)
            base = g.math('FLOOR', texel)
            axes.append((g.math('SUBTRACT', texel, base),
                         g.math('DIVIDE', g.math('ADD', base, 0.5), extent),
                         g.math('DIVIDE', g.math('ADD', base, 1.5), extent)))
        (fx, u0, u1), (fy, v0, v1) = axes
        taps = []
        for tap_v in (v0, v1):
            for tap_u in (u0, u1):
                nd = self._teximage(g, fetch, image, images, force_closest=True)
                # 二维采样不读 z:接一个现成的值,免得常量块为它再占一格。
                g._set(nd.inputs['Vector'], g.comb(tap_u, tap_v, tap_u))
                taps.append(nd)
        top = g.mixv(fx, taps[0].outputs['Color'], taps[1].outputs['Color'])
        bot = g.mixv(fx, taps[2].outputs['Color'], taps[3].outputs['Color'])
        top_a = g.mixf(fx, taps[0].outputs['Alpha'], taps[1].outputs['Alpha'])
        bot_a = g.mixf(fx, taps[2].outputs['Alpha'], taps[3].outputs['Alpha'])
        return g.mixv(fy, top, bot), g.mixf(fy, top_a, bot_a), taps[0]

    @staticmethod
    def _feed(g, heads, sock, color, alpha):
        # 🔴 颜色与 alpha 是**两片独立的答案叶**,必须各自寻址。段化 + 死代码消除之后,
        # 一个段完全可能只保留其中一片(只消费 alpha、颜色被裁),此时「颜色口不在就跳过」
        # 会把 alpha 一并漏喂 —— 它静默停在声明缺省 1.0 上,baseAlpha 恒 1,整张材质发白。
        for name, value in ((sock, color), (sock + '_alpha', alpha)):
            if value is None:
                continue
            for c in heads:
                # 容缺按名寻址:不是每段都有这片叶;但「接口在却取不到」必须炸,防名字错静默。
                head = c.inputs.get(name)
                if head is None:
                    if any(i.identifier == name or i.name == name
                           for i in c.node_tree.interface.items_tree):
                        raise RuntimeError('[ruri] 接口有 %s 却取不到 socket:接线面与组树脱节' % name)
                    continue
                g._set(head, value)

    @staticmethod
    def _wire_crossings(g, insts, rows):
        # 跨段活跃值:段 a 的 X 输出接段 b 的 X 输入。结构性存在,缺一头即拒绝。
        for name, _vec, frm, to in rows:
            src = insts[frm].outputs.get(name)
            dst = insts[to].inputs.get(name)
            if src is None or dst is None:
                raise RuntimeError('[ruri] 跨段接口 %s 缺失(段 %d -> %d):产物与清单脱节' % (name, frm, to))
            g._set(dst, src)

    def _wire_fetch(self, g, part, insts, fetch, image, images):
        src = insts[fetch['depth']]
        heads = insts[fetch['depth'] + 1:]
        if fetch['env']:
            if image is None:
                return None
            mip = src.outputs[fetch['sock'] + '_mip'] if fetch['mip'] else None
            color, alpha = g.env_image(image, src.outputs[fetch['sock'] + '_dir'], mip)
            self._feed(g, heads, fetch['sock'], color, alpha)
            return None
        color, alpha, anchor = self._sample(g, part, fetch, image, src.outputs[fetch['sock'] + '_uv'], images)
        self._feed(g, heads, fetch['sock'], color, alpha)
        return anchor

    def _wire_capability(self, g, insts, cap, ctx):
        """环境询问兑现:按能力查 CAP_BUILDERS。答不出 = 什么都不接,
        socket 停在能力自声明缺席值上,大声记一笔;半份询问/半份答案一律炸。"""
        src = insts[cap['depth']]
        heads = insts[cap['depth'] + 1:]
        builder = CAP_BUILDERS.get(cap['cap'])
        if builder is None:
            print('[ruri-cap] {0} 无原生等价物 → 缺席值'.format(cap['cap']), flush=True)
            return
        query = {}
        for name in cap['query']:
            out = src.outputs.get(cap['sock'] + '_' + name)
            if out is None:
                raise RuntimeError('[ruri-cap] {0}: 询问 socket {1}_{2} 不在组接口上'.format(
                    cap['cap'], cap['sock'], name))
            query[name] = out
        before = set(n.as_pointer() for n in g.t.nodes)
        answer = builder(g, query, dict(ctx, results=cap['results'], absent=cap['absent']))
        if answer is None:
            print('[ruri-cap] {0} 本场景答不出 → 缺席值'.format(cap['cap']), flush=True)
            return
        for nd in g.t.nodes:
            if nd.as_pointer() not in before:
                nd['ruri_cap'] = cap['cap']
        missing = [leaf for leaf in cap['results'] if leaf not in answer]
        if missing:
            raise RuntimeError('[ruri-cap] {0}: 建图器少答了结果叶 {1}'.format(cap['cap'], missing))
        for leaf, value in answer.items():
            name = cap['sock'] + ('_' + leaf if leaf else '')
            for c in heads:
                s = c.inputs.get(name)
                if s is not None:
                    g._set(s, value)

    def level_global_bases(self):
        """本栈从关卡世界取值的引擎全局:名 → (陈述者, 缺省)。陈述者是配方来源 —— level_global 由关卡的环境相位陈述,
        volume_global 由相机所在的体积栈陈述 —— 宿主按它分组核对「要么全给要么不给」;按「值 - 缺省」写,生成的组在里面
        读回再加缺省。"""
        return {name: (spec['src'], [float(value) for value in spec['base']])
                for name, spec in (self.engine_global_sources or {}).items()}

    def object_attribute_bases(self):
        """本栈逐物体读的引擎全局及各自缺省:宿主按物体写「值 - 缺省」的自定义属性,组里读回再加缺省。"""
        return {name: [float(value) for value in base] for name, base in (self.object_attributes or {}).items()}

    def texel_size_bases(self):
        """本栈读的「图名_TexelSize」及各自缺省:随关卡原尺寸建的关卡图每次按载荷改尺寸,宿主就把
        (1/宽, 1/高, 宽, 高) 按「值 - 缺省」写成视图层属性,组里读回再加缺省。"""
        return {name: [float(value) for value in base] for name, base in (self.texel_sizes or {}).items()}

    def _wire_zone(self, g, insts, zone, images, inst_sink, part, ctx):
        feed = insts[zone['depth']]
        heads = insts[zone['depth'] + 1:]
        zin = g._nd('GeometryNodeRepeatInput')
        zout = g._nd('GeometryNodeRepeatOutput')
        zin.pair_with_output(zout)
        zout.repeat_items.clear()
        for item, is_vec in zone['states']:
            zout.repeat_items.new('VECTOR' if is_vec else 'FLOAT', item)
        g._set(zin.inputs['Iterations'], feed.outputs[zone['sock'] + '_it'])
        for item, _is_vec in zone['states']:
            out = feed.outputs.get(zone['sock'] + '_s_' + item)
            if out is not None:
                g._set(zin.inputs[item], out)
        binsts = []
        for i, body_name in enumerate(zone['bodies']):
            b = g._nd('ShaderNodeGroup')
            b.node_tree = self.group(body_name)
            binsts.append(b)
            inst_sink.append(b)
            b['ruri_zone'] = zone['sock']
            b['ruri_binst'] = i
            for item, _is_vec in zone['states']:
                sock = b.inputs.get('s_' + item)
                if sock is not None:
                    g._set(sock, zin.outputs[item])
            for rname, _is_vec in zone['reads']:
                sock = b.inputs.get('r_' + rname)
                out = feed.outputs.get(zone['sock'] + '_r_' + rname)
                if sock is not None and out is not None:
                    g._set(sock, out)
        self._wire_crossings(g, binsts, zone['body_crossings'])
        for c in zone['capabilities']:
            self._wire_capability(g, binsts, c, ctx)
        for f in zone['fetches']:
            src = binsts[f['depth']]
            heads_b = binsts[f['depth'] + 1:]
            image = images.get(f['slot']) if images else None
            if f['env']:
                if image is None:
                    continue
                mip = src.outputs[f['sock'] + '_mip'] if f['mip'] else None
                color, alpha = g.env_image(image, src.outputs[f['sock'] + '_dir'], mip)
                self._feed(g, heads_b, f['sock'], color, alpha)
                continue
            color, alpha, _anchor = self._sample(g, part, f, image, src.outputs[f['sock'] + '_uv'], images)
            self._feed(g, heads_b, f['sock'], color, alpha)
        for item, seat in zone['body_finals'].items():
            out = binsts[seat[0]].outputs.get('o_' + item)
            if out is not None:
                g._set(zout.inputs[item], out)
        for c in heads:
            for item, _is_vec in zone['states']:
                sock = c.inputs.get(zone['sock'] + '_o_' + item)
                if sock is not None:
                    g._set(sock, zout.outputs[item])

    # ==================== 材质装配 ====================

    ANCHOR_SLOTS = ('_BaseMap', '_BaseColorMap', '_MainTex')

    CULL_LABEL = 'RuriCull'

    def _cull_transparency(self, g, cull, outline_fac):
        """剔除面的透明度(1 = 这一面整个不画)。它直接乘进 alpha,不再另起一级 MixShader:
        少一个闭包混合与一个 Transparent BSDF,而且 alpha 口的直接生产者永远是一个 Math 节点 ——
        宿主的灯链接校验按「先遇到谁」给节点打标志,MixShader 的因子口排在两个 Shader 口前面,
        alpha 若直接来自吃灯答案的那段组,那段组会先经因子口被打成「只够到出口」,整条灯链随之
        判非法(codegen 对非法入链的节点 need_exec=0,材质丢主光);隔一个 Math 节点,它就先经
        Light Accumulation 那一支被遇到。

        剔除哪一面(Unity CullMode:0 不剔、1 剔正面、2 剔背面)是逐材质的渲染状态,住在一枚 RuriCull 值节点上、
        实例化时照材质写:模板不按它分叉 —— 同一 part 的正反剔除共用一份模板,开文件少建一半模板图。三种各算一支,
        按值取一支;因子恒为 0 或 1、三支都有限,插值逐位等于被取的那一支。"""
        cgeo = g._nd('ShaderNodeNewGeometry')
        bf = cgeo.outputs['Backfacing']
        mode = g._nd('ShaderNodeValue')
        mode.label = self.CULL_LABEL
        mode.outputs[0].default_value = float(cull)
        front = g.math('SUBTRACT', 1.0, bf)
        back = g.math('ABSOLUTE', g.math('SUBTRACT', bf, outline_fac))
        neither = g.math('MULTIPLY', outline_fac, front)
        culls_front = g.math('COMPARE', mode.outputs[0], 1.0, 0.25)
        culls_back = g.math('COMPARE', mode.outputs[0], 2.0, 0.25)
        return g.mixf(culls_front, g.mixf(culls_back, neither, back), front)

    def _set_cull(self, mat, cull):
        """实例照自己的剔除写那枚 RuriCull(见 _cull_transparency);与模板里那份相同就不写。"""
        node = next((one for one in mat.node_tree.nodes if one.label == self.CULL_LABEL), None)
        if node is None:
            raise RuntimeError('[ruri-uber] 材质 {0} 的图里没有剔除值节点 {1}'.format(mat.name, self.CULL_LABEL))
        if node.outputs[0].default_value != float(cull):
            node.outputs[0].default_value = float(cull)

    def build_material(self, mat, part=None, opaque=True, blend=None, cull=2.0, images=None):
        part = part or self.DEFAULT_PART
        spec = self.part(part)
        # 渲染方式先于建图落在这张材质上(唯一写点;实例是模板拷贝,随之继承):能力兑现按它选解法,
        # 混合材质不进深度缓冲,问身后那张面要走原生 Raycast。
        mat.surface_render_method = self._render_method(part, opaque, blend)
        nt = mat.node_tree
        nt.nodes.clear()
        g = G(nt, is_group=False)
        # 原生灯节点与它们的换算链全部由兑现面(能力答案 / 闭包)在各自的标记区里建,不在这里预建:
        # 预建的那份不带 ruri_cap,重接时留下来变成悬空的灯链,校验器按灯节点的标志把它连同活链
        # 一起判非法,codegen 就把活链的下游节点整个禁掉 —— 实测重接后材质丢主光。
        insts = []
        for name in spec['segments']:
            grp = g._nd('ShaderNodeGroup')
            grp.node_tree = self.group(name)
            grp.width = 320
            insts.append(grp)
        geo = g.geo()
        tc = g.texco()
        tan_ws, tan_w = g.tbn()
        col = g.attr('Color')
        uv1 = g._nd('ShaderNodeUVMap')
        uv1.uv_map = 'UV1'
        uv2 = g._nd('ShaderNodeUVMap')
        uv2.uv_map = 'UV2'
        stmap = g._nd('ShaderNodeMapping')
        stmap.label = self.ST_NODE
        g._set(stmap.inputs['Vector'], tc.outputs['UV'])
        olattr = g._nd('ShaderNodeAttribute')
        olattr.attribute_name = 'ruri_outline'
        os_sep = g._nd('ShaderNodeSeparateXYZ')
        g._set(os_sep.inputs['Vector'], tc.outputs['Object'])
        os_comb = g._nd('ShaderNodeCombineXYZ')
        g._set(os_comb.inputs['X'], os_sep.outputs['X'])
        g._set(os_comb.inputs['Y'], os_sep.outputs['Z'])
        g._set(os_comb.inputs['Z'], os_sep.outputs['Y'])
        wires = {
            '_RuriOutlineShellGate': olattr.outputs['Fac'],
            'input_uv': stmap.outputs['Vector'],
            'input_uv1': uv1.outputs['UV'],
            'input_uv2': uv2.outputs['UV'],
            'input_normalWS': g.surface_normal(),
            'input_positionWS': geo.outputs['Position'],
            'input_positionOS': os_comb.outputs['Vector'],
            'input_tangentWS': tan_ws,
            'input_tangentWS_w': tan_w,
            'input_color': col.outputs['Color'],
            'input_color_w': col.outputs['Alpha'],
            'facing': g.facing_sign(),
        }
        for grp in insts:
            for s in grp.inputs:
                if s.name in wires:
                    g._set(s, wires[s.name])
        self._wire_crossings(g, insts, spec['crossings'])
        all_insts = list(insts)
        # 建图序事后从节点表恢复不出来:实例序与 part 名建的时候就烙上(重接按 depth 取实例)。
        for i, grp in enumerate(insts):
            grp['ruri_inst'] = i
        mat['ruri_uber_part'] = part
        # 栈身份烙在**建图处**(唯一写点;实例化是模板拷贝,自带继承)。判据不能用 ruri_uber_part:
        # 那个键每个生成栈都写,拿它认领就是一张材质被 N 个栈同时认领。模板材质同样要烙 ——
        # 不烙则重接面(换灯/换世界)认不出自己刚建的模板。
        mat['ruri_uber_stack'] = self.PANEL_KEY
        anchor = None
        for f in spec['fetches']:
            image = images.get(f['slot']) if images else None
            nd = self._wire_fetch(g, part, insts, f, image, images)
            if nd is not None and nd.image is not None:
                if anchor is None or (f['slot'] in self.ANCHOR_SLOTS and (anchor.label not in self.ANCHOR_SLOTS)):
                    anchor = nd
        for c in spec['capabilities']:
            self._wire_capability(g, insts, c, {'material': mat})
        for z in spec['zones']:
            self._wire_zone(g, insts, z, images, all_insts, part, {'material': mat})
        if anchor is not None:
            nt.nodes.active = anchor
            anchor.select = True
        finals = spec['finals']
        self._wire_overlays(g, insts, finals)
        color_sock = self._final_color(insts, finals)
        alpha_sock = self._final_socket(insts, finals, 'ret_gBuffer0_w')
        clip_sock = self._final_socket(insts, finals, '__clip')
        cull_fac = self._cull_transparency(g, cull, olattr.outputs['Fac'])
        outp = g._nd('ShaderNodeOutputMaterial')
        self._wire_outline(g, mat, part, spec, images, olattr.outputs['Fac'], outp)
        mat[self.BLEND_KEY] = list(blend) if blend is not None else []
        if blend in MULTIPLY_BLENDS:
            # 乘法帧(真源的 Blend Zero SrcColor):内核终色**就是乘数**,这一趟没有任何受光项
            # (真源只写 baseColor,alpha 不参与那条混合方程)⇒ 出口只有一枚带颜色的 Transparent BSDF,
            # 剔除面把颜色透成白(乘 1 = 不动背景)。
            #
            # 🔴 不许再串一级 MixShader:实测(真材质 + 白底)哪怕因子恒 0、只取 Transparent 那一支,
            # 宿主也把整片算成**不透明黑**;把出口直接接到 Transparent BSDF 上,同一棵树立刻给出
            # 正确的乘数(0.6558,0.2532,0.4231,与真源公式逐位一致)。乘法帧因此不建闭包也不需要灯节点,
            # 但前提是终色真的无灯 —— 见生成期 UntaintLightFreeFinals(能力答案与灯循环两条都要换掉)。
            tr = g._nd('ShaderNodeBsdfTransparent')
            if color_sock is not None:
                g._set(tr.inputs['Color'], g.choosev(cull_fac, color_sock, (1.0, 1.0, 1.0)))
            g._set(outp.inputs[0], tr.outputs[0])
            g.layout()
            return all_insts
        keep = g.math('SUBTRACT', 1.0, cull_fac)
        # 表面 = AddShader(透射, 闭包):片元盖住帧的那一份(覆盖率)乘进闭包的输入(见 _wire_closure),
        # 透射由 Transparent BSDF 的颜色给。与「MixShader(覆盖率, 透射, 闭包)」的透射、闭包份额逐项相等,
        # 但**不许用 MixShader**:EEVEE 把灯节点包进逐灯的循环,而 MixShader 给闭包的权重是在循环外算的,
        # 因子只要是算出来的(不是常量)循环里就读成 0 —— 整片表面的光全没了。实测墙面:同一个恒 1 的
        # 因子,常量给 0.20,从节点算出来给 0;把覆盖率移进闭包输入后就是 0.20。
        if blend is None or blend in OVER_BLENDS:
            coverage = keep if (opaque or alpha_sock is None) else g.math('MULTIPLY', alpha_sock, keep)
            if clip_sock is not None:
                coverage = g.math('MULTIPLY', coverage, clip_sock)
            coverage = self._coverage(g, coverage)
            through = g._nd('ShaderNodeBsdfTransparent')
            g._set(through.inputs['Color'], g.vmath('SUBTRACT', (1.0, 1.0, 1.0), coverage))
        else:
            # 其余混合按「源 × A + 帧 × T」拆:A 乘进闭包的输入(见 _wire_closure),T 是透射色。
            # 剔除面与 clip 掉的片元整个不改帧(= 纯透射):覆盖率之外的那份透射恒 1。
            coverage = keep if clip_sock is None else g.math('MULTIPLY', keep, clip_sock)
            coverage = self._coverage(g, coverage)
            through = g._nd('ShaderNodeBsdfTransparent')
            g._set(through.inputs['Color'], g.mixv(
                coverage, (1.0, 1.0, 1.0), self._blend_transmission(g, blend, color_sock, alpha_sock)))
        seat = g._nd('ShaderNodeAddShader')
        seat.label = self.SURFACE_MIX_LABEL
        seat[self.CLOSURE_SEAT_KEY] = 1
        g._set(seat.inputs[0], through.outputs[0])
        g._set(seat.inputs[1], self._wire_closure(g, mat, insts, finals, alpha_sock, coverage))
        g._set(outp.inputs[0], seat.outputs[0])
        g.layout()
        return all_insts

    def _wire_overlays(self, g, insts, finals):
        """覆盖型能力的询问(出口名以 OVERLAY_OUTPUT_PREFIX 开头)逐像素存成同名 AOV,留给后处理追踪完再合成。
        一律按颜色 AOV 存(视层按颜色登记,标量接进颜色口铺满三通道,读它的第一个通道)。"""
        for name in sorted(finals):
            if not name.startswith(OVERLAY_OUTPUT_PREFIX):
                continue
            socket = self._final_socket(insts, finals, name)
            if socket is None:
                raise RuntimeError('[ruri-uber] 覆盖询问 {0} 不在组接口上'.format(name))
            node = g._nd('ShaderNodeOutputAOV')
            node.aov_name = name
            g._set(node.inputs['Color'], socket)

    # 描边视图段吃的顶点入口口名,与几何树的那一份(vertex_inputs)同源同口径:物体空间、宿主轴,切线 w 取 Unity 口径。
    OUTLINE_INPUTS = ('input_positionOS', 'input_normalOS', 'input_tangentOS', 'input_tangentOS_w', 'input_texcoord',
                      'input_texcoord1', 'input_texcoord2', 'input_color', 'input_color_w')

    def _wire_outline(self, g, mat, part, spec, images, shell, output):
        """描边视图段:真源描边趟的顶点入口落在材质的位移出口上,宿主在顶点着色阶段逐视图求值 —— 视矩阵是正在画的那个
        视图的,投影与像素数是宿主陈述在这个物体上的那个视图的。转视角只动 GPU,几何树一次都不重算。

        位移只落在壳上(几何树复制出来、ruri_outline = 1 的那一份),整段关在一个循环区里,次数 = ruri_outline ×
        (1 - Backfacing):本体顶点一次都不跑。片元阶段宿主为凹凸再求一遍位移,看得见的片元要么是本体(标记 0)要么是
        壳的背面(Backfacing 1),同样一次都不跑 —— 位移不动任何一个看得见的片元的法线,也不花它一分钱;顶点阶段的
        Backfacing 恒 0。顶点输入只把原样读数经循环区的状态项送进去,由它们派生的一切(物体空间法线、切线基、取点、
        反投影)都在区里。"""
        view = spec.get('outline')
        if not view:
            return
        insts = []
        for index, name in enumerate(view['segments']):
            grp = g._nd('ShaderNodeGroup')
            grp.node_tree = self.group(name)
            grp.width = 320
            grp['ruri_outline_inst'] = index
            insts.append(grp)
        wanted = [name for name in self.OUTLINE_INPUTS if any(grp.inputs.get(name) is not None for grp in insts)]
        raw = self._outline_reads(g, wanted)
        zin = g._nd('GeometryNodeRepeatInput')
        zout = g._nd('GeometryNodeRepeatOutput')
        zin.pair_with_output(zout)
        zout.repeat_items.clear()
        for key, (_socket, is_vec) in raw.items():
            zout.repeat_items.new('VECTOR' if is_vec else 'FLOAT', key)
        zout.repeat_items.new('VECTOR', 'offset')
        front = g.math('SUBTRACT', 1.0, g.geo().outputs['Backfacing'])
        g._set(zin.inputs['Iterations'], g.math('ROUND', g.math('MULTIPLY', shell, front)))
        for key, (socket, _is_vec) in raw.items():
            g._set(zin.inputs[key], socket)
        inside = self._outline_inputs(g, {key: zin.outputs[key] for key in raw}, wanted)
        for grp in insts:
            for name in wanted:
                sock = grp.inputs.get(name)
                if sock is not None:
                    g._set(sock, inside[name])
        self._wire_crossings(g, insts, view['crossings'])
        for fetch in view['fetches']:
            self._wire_fetch(g, part, insts, fetch, images.get(fetch['slot']) if images else None, images)
        seg, _vec = view['finals']['position']
        x, y, z = g.sep(insts[seg].outputs['position'])
        placed = g.vtrans(g.comb(x, z, y), 'OBJECT', 'WORLD', 'POINT')
        g._set(zout.inputs['offset'], g.vmath('SUBTRACT', placed, g.geo().outputs['Position']))
        g._set(output.inputs['Displacement'], zout.outputs['offset'])
        mat.displacement_method = 'BOTH'

    def _outline_reads(self, g, wanted):
        """顶点入口要的原样读数(循环区的状态项):名 → (socket, 是否向量)。只读,不派生。"""
        raw = {}
        if 'input_positionOS' in wanted:
            raw['position'] = (g.texco().outputs['Object'], True)
        if {'input_normalOS', 'input_tangentOS', 'input_tangentOS_w'} & set(wanted):
            raw['normal'] = (g.geo().outputs['Normal'], True)
        if {'input_tangentOS', 'input_tangentOS_w'} & set(wanted):
            raw['unit'] = (1.0, False)
        for name, layer in (('input_texcoord', 'UVMap'), ('input_texcoord1', 'UV1'), ('input_texcoord2', 'UV2')):
            if name in wanted:
                node = g._nd('ShaderNodeUVMap')
                node.uv_map = layer
                raw[name] = (node.outputs['UV'], True)
        if {'input_color', 'input_color_w'} & set(wanted):
            color = g.attr('Color')
            raw['color'] = (color.outputs['Color'], True)
            raw['alpha'] = (color.outputs['Alpha'], False)
        return raw

    def _outline_inputs(self, g, reads, wanted):
        """循环区里由原样读数派生的顶点入口:法线与切线从世界换回物体空间(宿主轴,单位长),切线基照 tbn 的做法取
        Normal Map 的两根轴(强度口吃区里的 1,节点因此落在区里),w 取 Unity 口径。"""
        out = {}
        if 'input_positionOS' in wanted:
            out['input_positionOS'] = reads['position']
        if 'input_normalOS' in wanted:
            out['input_normalOS'] = g.vmath('NORMALIZE', g.vtrans(reads['normal'], 'WORLD', 'OBJECT', 'NORMAL'))
        if {'input_tangentOS', 'input_tangentOS_w'} & set(wanted):
            axes = []
            for x, y in ((1.0, 0.0), (0.0, 1.0)):
                nd = g._nd('ShaderNodeNormalMap')
                nd.space = 'TANGENT'
                nd.uv_map = 'UVMap'
                g._set(nd.inputs['Strength'], reads['unit'])
                g._set(nd.inputs['Color'], (x * 0.5 + 0.5, y * 0.5 + 0.5, 0.5))
                axes.append(nd.outputs['Normal'])
            tangent, bitangent = axes
            out['input_tangentOS'] = g.vmath('NORMALIZE', g.vtrans(tangent, 'WORLD', 'OBJECT', 'VECTOR'))
            out['input_tangentOS_w'] = g.math('SIGN', g.vmath(
                'DOT_PRODUCT', g.vmath('CROSS_PRODUCT', tangent, reads['normal']), bitangent))
        for name in ('input_texcoord', 'input_texcoord1', 'input_texcoord2'):
            if name in wanted:
                out[name] = reads[name]
        if 'input_color' in wanted:
            out['input_color'] = reads['color']
        if 'input_color_w' in wanted:
            out['input_color_w'] = reads['alpha']
        return out

    def _coverage(self, g, coverage):
        """覆盖率落在一枚带标记的节点上:重接闭包时从这里取回同一个覆盖率(它不属于闭包,重接不重建)。"""
        node = g._nd('ShaderNodeMath')
        node.operation = 'ADD'
        node.label = self.SURFACE_COVERAGE_LABEL
        g._set(node.inputs[0], coverage)
        node.inputs[1].default_value = 0.0
        return node.outputs[0]

    @staticmethod
    def _blend_transmission(g, blend, color_sock, alpha_sock):
        """T:帧留下多少(逐通道)。目标因子直接给一份;源因子里乘到帧上的那部分(DstColor /
        OneMinusDstColor)挪过来,因为 源 × 帧 = 帧 × 源。帧的 alpha 宿主没有,读它的因子拒产。"""
        src, dst = blend
        if dst == BLEND_ZERO:
            kept = (0.0, 0.0, 0.0)
        elif dst == BLEND_ONE:
            kept = (1.0, 1.0, 1.0)
        elif dst == BLEND_SRC_ALPHA:
            kept = g.vmath('SCALE', (1.0, 1.0, 1.0), s=alpha_sock)
        elif dst == BLEND_ONE_MINUS_SRC_ALPHA:
            kept = g.vmath('SCALE', (1.0, 1.0, 1.0), s=g.math('SUBTRACT', 1.0, alpha_sock))
        elif dst == BLEND_SRC_COLOR:
            kept = color_sock
        elif dst == BLEND_ONE_MINUS_SRC_COLOR:
            kept = g.vmath('SUBTRACT', (1.0, 1.0, 1.0), color_sock)
        else:
            raise RuntimeError('[ruri-uber] 目标因子 {0} 读帧自己的颜色/alpha,宿主的透射兑现不了'.format(dst))
        if src == BLEND_DST_COLOR:
            return g.vmath('ADD', kept, color_sock)
        if src == BLEND_ONE_MINUS_DST_COLOR:
            return g.vmath('SUBTRACT', kept, color_sock)
        return kept

    @staticmethod
    def _blend_emission_scale(g, blend, color_sock, alpha_sock):
        """A:源自己乘上的那一份,(是否向量, 值);None = 1。源因子读帧的部分已挪进 T。"""
        if blend is None or blend in OVER_BLENDS:
            return None
        src = blend[0]
        if src in (BLEND_ONE, BLEND_ONE_MINUS_DST_COLOR):
            return None
        if src in (BLEND_ZERO, BLEND_DST_COLOR):
            return (False, 0.0)
        if src == BLEND_SRC_ALPHA:
            return (False, alpha_sock)
        if src == BLEND_ONE_MINUS_SRC_ALPHA:
            return (False, g.math('SUBTRACT', 1.0, alpha_sock))
        if src == BLEND_SRC_COLOR:
            return (True, color_sock)
        if src == BLEND_ONE_MINUS_SRC_COLOR:
            return (True, g.vmath('SUBTRACT', (1.0, 1.0, 1.0), color_sock))
        raise RuntimeError('[ruri-uber] 源因子 {0} 读帧自己的 alpha,宿主的透射兑现不了'.format(src))

    @staticmethod
    def _final_socket(insts, finals, name):
        seat = finals.get(name)
        return None if seat is None else insts[seat[0]].outputs.get(name)

    def _final_color(self, insts, finals):
        """终色就是 ret_gBuffer0 这一个出口:生成期按灯分账(无灯 / 循环没跑两份孪生)只对它做,出口不许拿别的口顶替。"""
        return self._final_socket(insts, finals, 'ret_gBuffer0')

    def _wire_closure(self, g, mat, insts, finals, alpha_sock=None, coverage_sock=None):
        """表面闭包是兑现面的一部分(Emission + Light Accumulation):与能力答案一样打 ruri_cap 标记、
        重接时整块回收重建。

        材质记着的混合若让源乘上一份 A(SrcAlpha One 之类),A 在这里乘进闭包的输入并随闭包一起
        打标记:两个闭包都对输入线性(逐灯求和),先乘后求和与真源的「源 × A」逐位同值。光泽瓣同样乘。
        A 读源色的(SrcColor 一类)无灯那一份按无灯终色取 A:它在光循环之外求值,不许读灯。"""
        before = set(n.as_pointer() for n in g.t.nodes)
        color_sock = self._final_color(insts, finals)
        unlit_sock = self._final_socket(insts, finals, UNLIT_FINAL)
        blend = self._stored_blend(mat)
        lit_scale = None if color_sock is None else self._blend_emission_scale(g, blend, color_sock, alpha_sock)
        unlit_scale = lit_scale
        if unlit_sock is not None and lit_scale is not None and lit_scale[0]:
            unlit_scale = self._blend_emission_scale(g, blend, unlit_sock, alpha_sock)

        def weighted(sock, scale):
            if sock is None:
                return None
            if scale is not None:
                vector, value = scale
                sock = g.vmath('MULTIPLY', sock, value) if vector else g.vmath('SCALE', sock, s=value)
            if coverage_sock is not None:
                sock = g.vmath('SCALE', sock, s=coverage_sock)
            return sock

        surface = self._surface_closure(
            g, weighted(color_sock, lit_scale),
            weighted(self._final_socket(insts, finals, NO_LOOP_FINAL), lit_scale),
            weighted(unlit_sock, unlit_scale),
            weighted(self._final_socket(insts, finals, GLOSSY_FINAL), lit_scale))
        for nd in g.t.nodes:
            if nd.as_pointer() not in before:
                nd['ruri_cap'] = self.CLOSURE_CAP
        return surface

    def _per_light_share(self, g, lit, unlit, noloop):
        """这一圈灯该进多少账。宿主的光循环对**每一盏灯**把整棵树跑一遍,不读灯的那一份(环境、自发光……)
        已在循环之外经 Emission 进过账(见 _surface_closure)。

        方向光那一圈(原生 is_sun)交「终色 − 无灯终色」= 按这盏灯算的主光着色(附加光项在那一圈被身份门乘零,
        不会与主光重复);其余每一圈交「终色 − 同一条终色但附加光循环没跑过」—— 正好是这盏灯自己的附加光贡献,
        同圈里那些与它无关的项(按这盏灯算出来的主光着色、环境、自发光)在相减时逐位抵消,不必知道内核怎么把那一项
        合进去。

        没有附加光循环的 part 发不出后一份终色,只在方向光那一圈进账(点光对它本来就没贡献)。"""
        is_main = _native_light(g)['is_main']
        main = g.vmath('SUBTRACT', lit, unlit)
        if noloop is None:
            return g.vmath('SCALE', main, s=is_main)
        return g.choosev(is_main, g.vmath('SUBTRACT', lit, noloop), main)

    def _surface_closure(self, g, lit, noloop, unlit, glossy):
        """表面闭包(各份都已乘好混合与覆盖率)。不读灯的那一份 —— 无灯终色,终色不读灯时就是终色本身 —— 经
        Emission 在光循环之外只交一次,视图里一盏灯都没有(材质预览默认不用场景灯)时材质照样显示它;读灯的部分进
        Light Accumulation:灯节点一进图整棵树就按灯逐圈求值,Diffuse Light = 这一圈灯自己那一份(见 _per_light_share),
        Diffuse Color 恒 1:内核终色已经是完整着色,不再乘任何反照率。入口交了光泽一份(GLOSSY_FINAL)就进 Glossy Light,
        只在方向光那一圈进账(宿主在这个口上原生乘灯的 Specular 滑杆);打开漫反射 / 光泽光照通道的视图层按瓣分开读,
        不读灯的那一份落在自发光通道。两个闭包实测都不钳值,HDR 原样通过。"""
        closures = []
        once = lit if unlit is None else unlit
        if once is not None:
            emission = g._nd('ShaderNodeEmission')
            g._set(emission.inputs['Color'], once)
            closures.append(emission.outputs[0])
        diffuse = None if (lit is None or unlit is None) else self._per_light_share(g, lit, unlit, noloop)
        if diffuse is not None or glossy is not None:
            accumulation = g._nd('ShaderNodeLightAccumulation')
            accumulation.label = NATIVE_LIGHT_LABEL
            accumulation.inputs['Diffuse Color'].default_value = (1.0, 1.0, 1.0, 1.0)
            if diffuse is not None:
                g._set(accumulation.inputs['Diffuse Light'], diffuse)
            if glossy is not None:
                accumulation.inputs['Glossy Color'].default_value = (1.0, 1.0, 1.0, 1.0)
                g._set(accumulation.inputs['Glossy Light'], g.vmath('SCALE', glossy, s=_native_light(g)['is_main']))
            closures.append(accumulation.outputs['Shader'])
        if not closures:
            nothing = g._nd('ShaderNodeEmission')
            nothing.inputs['Color'].default_value = (0.0, 0.0, 0.0, 1.0)
            return nothing.outputs[0]
        if len(closures) == 1:
            return closures[0]
        both = g._nd('ShaderNodeAddShader')
        g._set(both.inputs[0], closures[0])
        g._set(both.inputs[1], closures[1])
        return both.outputs[0]

    # ==================== 模板材质 + 实例化(唯一的逐材质路径) ====================

    TEMPLATE_KEY = 'ruri_uber_template'
    STAMP_KEY = 'ruri_uber_stamp'
    SURFACE_MIX_LABEL = 'RuriSurfaceMix'
    SURFACE_COVERAGE_LABEL = 'RuriSurfaceCoverage'
    # 闭包落在表面混合节点(AddShader)的哪一个口。
    CLOSURE_SEAT_KEY = 'ruri_closure_input'
    # 材质兑现时用的真源混合因子 [源, 目标](Unity BlendMode 值);空 = 家族缺省的 over / 不透明。
    BLEND_KEY = 'ruri_uber_blend'
    CLOSURE_CAP = 'closure'

    def _stored_blend(self, mat):
        stored = mat.get(self.BLEND_KEY)
        if stored is None:
            raise RuntimeError('[ruri-uber] 材质 {0} 没记混合因子(旧产物),请重新导入'.format(mat.name))
        stored = [int(x) for x in stored]
        return tuple(stored) if stored else None

    # ==================== 跨应用参数共享的自述 ====================
    # 参数行分三个桶存,而着色器只有一套平的 uniform 名字 —— 差异只有 ST 的 `_ST` 后缀
    # (桶键是槽名 `_BaseMap`,uniform 是 `_BaseMap_ST`)。这条规则的真源在这里,所以
    # 由材质**自述**带走:任何跨应用的搬运者读这一条就能把行摊平成着色器词汇,不必认识
    # ruri_uber_* 一个字。搬运者自己抄一张桶表 = 第二处真源,加一个桶就静默丢一整类值。
    # ruri_uber_images 不在行里:图不是 uniform,是通道,走各宿主自己的贴图路由。
    SHADING_KEY = 'ruri_shading'
    SHADING_ROW = {
        'ruri_uber_floats': '{0}',
        'ruri_uber_colors': '{0}',
        'ruri_uber_st': '{0}_ST',
    }

    def _declare_shading(self, mat):
        """本材质说清楚:它讲谁的着色器词汇、是哪个变体、行与图各在哪、图的通道里装着什么。

        贴图那半是给别的应用看的:本腿按槽名逐通道原样采,不需要语义;而"这张图该进
        Painter 的哪个通道"只能由 [TexturePacking] 自述回答 —— 槽名是游戏词汇
        (_BumpMap 与 _NormalMap 同义不同名),按名猜必错且静默。带本材质这一形态会采的
        每个槽,绑没绑图都算:别处画了一张这边还空着的图,要有地方接进来;已绑哪些看记录本身。"""
        packing = self.m.get('packing') or {}
        bound = dict(mat.get('ruri_uber_images') or {})
        part = str(mat.get('ruri_uber_part', ''))
        sampled = set(bound)
        if part in self.m['parts']:
            sampled.update(fetch['slot'] for fetch in self._part_fetches(self.part(part)))
        # 本腿把 part 编译成各自的树,所以这个值在这边是编译期常量、不是参数行里的一项。
        # 别的宿主把整族编译成一个着色器,靠一个 uniform 选分支;它取缺省 0 = 整张脸按
        # Standard 渲,而且零报错。所以照实报出来,让它随行一起走。
        constants = {}
        variant_uniform = self.m.get('variant_uniform') or ''
        variant_value = (self.m.get('variant_values') or {}).get(part)
        if variant_uniform and variant_value is not None:
            constants[variant_uniform] = variant_value
        # 行里存的是**作者值**(游戏 Properties 的 [Gamma] 那一族),而着色器读的是线性值 ——
        # 本腿在 _param_matrix 里补那一次 srgb→linear。别的应用拿到的是同一份作者值,不补就
        # 整条颜色链差一条 gamma 曲线,而且两边名字全对得上、一个字都不报。
        # 只列本材质行里真有的那些,不整份 SRGB_PARAMS 抄过去。
        gamma = sorted(
            name for name in self.SRGB_PARAMS
            if name in (mat.get('ruri_uber_floats') or {})
            or name in (mat.get('ruri_uber_colors') or {}))
        # 材质关键字的取值就是它在本材质上的那个 0/1 uniform(建图时按材质自己的关键字表写入)。
        floats = mat.get('ruri_uber_floats') or {}
        for keyword in self.m.get('material_keywords') or ():
            constants[keyword] = float(floats.get(keyword, 0.0))
        mat[self.SHADING_KEY] = {
            'shader': self.PANEL_KEY,
            # 着色器身份:风格参数面的散列,Substance 腿把同值写在着色器清单里。相等 ⇒ 两边是同一个
            # 着色器,整行参数原样搬;不等 ⇒ 只有同名参数敢搬。只有画材质的栈声明它(后处理栈不画材质、
            # 清单里也就没有),所以在这里读,不在构造时读。
            'identity': self.m['identity'],
            'constants': constants,
            'gamma': gamma,
            # 本栈的着色器叫什么。别的应用拿它去自己的货架上找同名着色器 —— 各腿的产物同名
            # 是配方 MaterialName 决定的(Substance 腿产出 <MaterialName>.glsl),不是巧合;
            # 找不到就响亮地说,不静默把参数喂给一个别的着色器。
            'name': self.MATERIAL_NAME,
            'variant': str(mat.get('ruri_uber_part', '')),
            'values': dict(self.SHADING_ROW),
            'images': {
                'group': 'ruri_uber_images',
                'packing': {slot: packing[slot] for slot in sorted(sampled) if slot in packing},
            },
            # 引擎全局:本着色器从场景的世界上读的那些量,各带缺省。宿主在世界上记「值 - 缺省」,图里读回再加缺省;
            # 别的应用照这张表取本场景此刻的实际值。它们不跟材质走,却决定材质画成什么样 —— 别处的着色器实例
            # 只认自己存下的那份,换一代缺省它也不动,两边就各按各的环境色与阴影强度画。
            'engine': {name: list(base) for name, (_statement, base) in self.level_global_bases().items()},
        }

    def render_footprint_attributes(self):
        """本栈读渲染像素世界尺寸的场景视层属性:询问了屏幕半径上限的遮蔽要它;宿主按渲染相机写上去。"""
        for part in self.m['parts'].values():
            for cap in list(part['capabilities']) + [cap for zone in part['zones'] for cap in zone['capabilities']]:
                if cap['cap'] == 'ScreenSpaceOcclusion' and 'pixel_radius' in cap['query']:
                    return [RENDER_FOOTPRINT_ATTRIBUTE]
        return []

    # 顶点树上的这一格 = 这个物体有描边壳,它的材质在位移出口里读视图窗口。
    VIEW_READER_KEY = 'ruri_view_reader'

    def view_window_attributes(self):
        """本栈读「正在画的那个视图」的物体属性(描边视图段):投影四列与其逆四列、近远裁剪与正交、像素宽高,名字见
        VIEW_WINDOW。宿主按用户在看的视口、渲染时按渲染相机写到读它的物体上;没有视图段的栈什么都不读。"""
        if any(part.get('outline') for part in self.m['parts'].values()):
            return VIEW_WINDOW
        return None

    def view_window_readers(self, scene):
        """读视图窗口的物体:本栈的顶点腿给了描边壳的那些。宿主在开文件、撤销、新壳建成之后问一次,不逐拍扫。"""
        found = []
        for obj in scene.objects:
            if obj.type != 'MESH':
                continue
            modifier = obj.modifiers.get(self.VTX_MODIFIER)
            tree = modifier.node_group if modifier is not None else None
            if tree is not None and tree.get(self.VIEW_READER_KEY):
                found.append(obj)
        return found

    def light_parameter_attributes(self):
        """本栈逐灯读的源管线参数:每个向量在灯上的属性名,按结构体的字段序 —— 也就是关卡陈述逐灯给出那几个向量的次序。
        宿主导入灯时按这份名字写上去;没有 SourceLightParameters 询问的栈不读任何参数。"""
        found = []
        for part in self.m['parts'].values():
            for cap in list(part['capabilities']) + [cap for zone in part['zones'] for cap in zone['capabilities']]:
                if cap['cap'] != 'SourceLightParameters':
                    continue
                names = [LIGHT_PARAMETER_ATTRIBUTE.format(leaf) for leaf, vector in cap['results'].items() if vector]
                if found and names != found:
                    raise RuntimeError('[ruri-cap] 本栈两处灯参数询问的向量不一致:{0} 与 {1}'.format(found, names))
                found = names
        return found

    def level_image_layouts(self):
        """本栈读的关卡图(3D 纹理、带 mip 的纹理数组、uniform 数据表)及其铺法,宿主据此建图、铺格、填边。"""
        return dict(self.level_images)

    def _level_image(self, slot):
        """关卡图:全场共享一张图(引擎全局,不随材质变)。图只有宿主一个建造者 —— 它也是写像素的那个,
        两处各建一份,尺寸/格式/色彩空间迟早分叉。"""
        return getattr(self._host_module(), self.host['volume_image_fn'])(self.level_images[slot])

    def _template_images(self, part):
        """模板建满全部采样槽;没绑真图的槽 = 颜色 == 该割点 neutral 的 1x1 图
        (与直建路缺图语义逐位等价;占位图的槽语义中性不是一回事,拿错整脸黑,实锤)。"""
        rows = [fetch for fetch in self._part_fetches(self.part(part)) if not fetch['env']]
        out = {}
        for fetch in rows:
            if fetch['slot'] in out:
                continue
            out[fetch['slot']] = _neutral_image(
                fetch['neutral'], fetch['neutral_alpha'], fetch['non_color'])
        return out

    @staticmethod
    def _part_fetches(spec):
        """一个 part 在材质上的全部取点:片元段的、循环体的、描边视图段的。"""
        rows = list(spec['fetches'])
        for zone in spec['zones']:
            rows.extend(zone['fetches'])
        rows.extend((spec.get('outline') or {}).get('fetches') or ())
        return rows

    def _template(self, part, opaque, blend):
        """模板按 (part, 透明形态, 混合) 分;剔除不分(实例自己写 RuriCull,见 _cull_transparency),参数与开关不分
        (实例写成组输入的常量,宿主内联时按它折叠与剪枝,见 apply_params)。"""
        name = '{0}{1} {2}b{3}'.format(self.TEMPLATE_MAT, part, int(bool(opaque)),
                                       '-' if blend is None else '{0}.{1}'.format(*blend))
        tpl = bpy.data.materials.get(name)
        if tpl is not None and tpl.get(self.STAMP_KEY) == self.STAMP and tpl.node_tree is not None:
            return tpl
        # 模板是插件数据:本模块这一生建的那张认 stamp;同名的别的(上一次注册留下的)清掉重建。实例是拷贝,不是它的用户。
        if tpl is not None:
            bpy.data.materials.remove(tpl)
        tpl = _host().plugin_data(bpy.data.materials.new(name))
        if tpl.node_tree is None:
            tpl.use_nodes = True
        begun = time.perf_counter()
        self.build_material(tpl, part=part, opaque=opaque, blend=blend, images=self._template_images(part))
        tpl[self.STAMP_KEY] = self.STAMP
        tpl[self.TEMPLATE_KEY] = 1
        print('[ruri-uber] 模板 {0}:{1} 节点 {2} 连线,建图 {3:.1f} s'.format(
            name, len(tpl.node_tree.nodes), len(tpl.node_tree.links), time.perf_counter() - begun), flush=True)
        return tpl

    def _render_method(self, part, opaque, blend):
        """EEVEE 的透明解法必须跟着 build_material 的 alpha 判据走(同一个 opaque)。

        opaque ⇒ alpha 恒 1(或 clip 出来的 0/1 二值),'DITHERED' 保住真深度、景深与光追,
        二值 alpha 下不产生任何抖动噪点。非 opaque ⇒ alpha 是连续值,'DITHERED' 会把它按
        蓝噪声随机取舍,收敛前满屏噪点(视口里尤其难看)—— 真源那趟就是 Blend 混合,
        'BLENDED' 才是等价物。乘法帧(Blend Zero SrcColor)是带颜色的 Transparent BSDF,
        拆开的混合(加色、预乘……)是透射加闭包,同理只能走 BLENDED。**判据只许在这里算一次**:早先渲染方式读 part 的 Transparent 声明、
        alpha 读材质的 _SurfaceType,两套判据一分岔,_SurfaceType=1 却落在非透明 part 的件
        (如 CharacterNPR 的半透明裙摆)就是连续 alpha 配抖动解法 = 噪点透明。

        壳堆是这条规则的例外,而且是宿主能力差异不是参数选择:BLENDED 不写深度、不做逐片元
        排序,几十层自我重叠的壳一旦绘制序翻转,合成就地变不透明 —— 渲染出来是一圈硬边亮楔子,
        且对任何 fur 参数都不响应(实测 24 层壳逐一消融 _FurCutoffEnd/_FurEdgeFade/_UseBumpMap/
        _FurNoise 全无变化,换成 DITHERED 当场干净);真源本身就是抖动裁切的 part 同理,它的 alpha 是覆盖率。
        两者都按 part 自己的 StochasticAlpha 声明认。"""
        if self.PART_META.get(part, {}).get('stochastic_alpha'):
            return 'DITHERED'
        return 'DITHERED' if (opaque and (blend is None or blend in OVER_BLENDS)) else 'BLENDED'

    def instantiate(self, name, part, images=None, opaque=True, blend=None, cull=2.0):
        """一张材质 = 模板拷贝 + 贴图指针 + 剔除值;参数由调用方写完记录后经 apply_params 写成常量。零建图。
        灯上的那一格(主光身份、朝向)不在这里烙:新材质进场是派生态调度器的一件事实,那一批落地时刷一次。"""
        tpl = self._template(part, opaque, blend)
        # 模板是插件数据,拷出来的实例是内容:随文件走的是它的记录,它的树开文件时照记录重编。
        mat = _host().content(tpl.copy())
        mat.name = name
        self._set_cull(mat, cull)
        for key in (self.TEMPLATE_KEY, self.STAMP_KEY):
            if key in mat:
                del mat[key]
        swapped = 0
        if images and mat.node_tree is not None:
            for nd in mat.node_tree.nodes:
                if nd.type != 'TEX_IMAGE':
                    continue
                real = images.get(_slot_of(nd.label or ''))
                if real is not None:
                    _swap_image(nd, real, images)
                    swapped += 1
        # 随绑定真图走的表列(模板按占位图建);漏了 ramp/LUT 的 UV 整体错位。
        sizes = self._image_rows(part, images)
        if sizes:
            merged = _mixed(mat.get('ruri_uber_colors'))
            merged.update(sizes)
            mat['ruri_uber_colors'] = merged
        # 基座骨播种。落在这里而不是 build_material:模板按 part 共享,基座骨是逐材质的自定义属性,
        # 不是模板的一部分。
        rig_prop = self.RIG.get('prop') or ''
        if rig_prop and part in set(self.RIG.get('parts') or []):
            mat[rig_prop] = self.RIG.get('bone') or ''
        return mat, swapped

    # ==================== provider ====================

    # 材质记录:导入时从游戏材质写下、面板改的就是这几格 —— 它们是内容,随文件走;编出来的树不是(见 _compile)。
    RECORD_KEYS = ('ruri_uber_part', 'ruri_uber_images', 'ruri_uber_floats', 'ruri_uber_colors', 'ruri_uber_st',
                   'ruri_uber_disabled_passes', 'ruri_uber_shader_passes', 'ruri_uber_shader_guid', 'ruri_uber_shader')
    # 上一版产物写在实例上、如今不再由实例带着的键:照记录重编时不带过去。
    SUPERSEDED_KEYS = ('ruri_uber_stamp', 'ruri_param_col', 'ruri_uber_signature', 'ruri_closure_engine',
                       'ruri_main_light')

    def _record_float(self, floats, source_name):
        """记录里一条真源属性的值:记录按图的词汇存(见 _projected),真源 pass 的状态词与剔除开关说的是游戏词汇。"""
        return floats.get(self.SOURCE_NAMES.get(source_name, source_name))

    def _pass_word(self, word, floats, what, material):
        """真源 pass 的一个状态词:定值原样,[属性] 取材质记录里的值。"""
        kind, value = word
        if kind == 'fixed':
            return int(value)
        stated = self._record_float(floats, value)
        if stated is None:
            raise RuntimeError('[ruri-uber] 材质 {0} 没有 {1}:真源 pass 的 {2} 取它的值'.format(
                material, value, what))
        return int(round(float(stated)))

    def _record_blend(self, meta, floats, material):
        """(源因子, 目标因子);part 没照抄真源的 Blend 行 = None(家族缺省)。"""
        words = meta.get('blend')
        if not words:
            return None
        return tuple(self._pass_word(word, floats, 'Blend', material) for word in words)

    def _record_cull(self, meta, floats, material):
        declared = meta.get('cull')
        if declared:
            return float(self._pass_word(declared, floats, 'Cull', material))
        if self.CULL_TWO_SIDED:
            switch = self.CULL_TWO_SIDED['property']
            stated = self._record_float(floats, switch)
            if stated is None:
                raise RuntimeError('[ruri-uber] 材质 {0} 没有 {1}:本家族的剔除取这个双面开关'.format(
                    material, switch))
            return float(self.CULL_TWO_SIDED['on' if round(float(stated)) != 0 else 'off'])
        if not self.CULL_PROPERTY:
            return self.CULL_FIXED
        value = self._record_float(floats, self.CULL_PROPERTY)
        if value is None:
            print('[ruri-uber] !! {0} 未声明 {1},按双面渲染'.format(
                material, self.CULL_PROPERTY), flush=True)
            return 0.0
        return float(value)

    @staticmethod
    def _shader_name(builder, props):
        return builder.shader_display_name(props)

    def _variant(self, builder, props):
        """(part 名, part id);非本风格返回 None。认领判据 = m_Shader 身份,属性指纹判变体已废除禁回退。"""
        name = self._shader_name(builder, props)
        if name is None:
            ref = props.shader_ref if isinstance(props.shader_ref, dict) else {}
            print('[ruri-uber] !! 0DAY: material {0} 的 m_Shader {1}/{2} 解析不出自称名 —— 拒绝按指纹猜,交宿主兜底'
                  .format(props.name, ref.get('guid'), ref.get('fileID')), flush=True)
            return None
        if name in self.NON_SHADING:
            return NON_SHADING
        fallback = None
        for part, meta in self.PART_META.items():
            if meta['shader'] != name and name not in meta['aliases']:
                continue
            disc = meta['discriminator']
            if disc is None:
                fallback = (part, meta['id'])
            elif props.floats.get(disc):
                return (part, meta['id'])
        return fallback

    def _load_images(self, builder, props):
        images = {}
        load = getattr(builder, self.host.get('load_image_fn', '_load_image'))
        for name, guid in props.textures.items():
            img = load(guid)
            if img is None:
                print('[ruri-uber] !! {0}: texture {1} guid={2} LOAD FAILED'.format(
                    props.name, name, guid), flush=True)
                continue
            try:
                if img.alpha_mode != 'CHANNEL_PACKED':
                    img.alpha_mode = 'CHANNEL_PACKED'
            except Exception:
                pass
            images[name] = img
        return images

    def _projected(self, table):
        """把材质自述的一张表从游戏的词汇翻成本图的词汇。

        两边天然同名的游戏没有对照表,这里是恒等、原样交回,一次拷贝都不做。有对照的只改名字、
        值一个不动;没列进对照的名字原样留着 —— 图上没有它的口,本来就读不到,丢掉反而让面板上
        「这张材质到底带了什么」少一截。同名相撞时声明过的那条赢:游戏拿同一个拼法装了别的东西
        (鸣潮的材质里真有一条叫 _BumpMap 的槽,指的是特效流动图),让它压掉译过来的才是错的。"""
        if not self.SOURCE_NAMES:
            return table
        renamed = {name: value for name, value in table.items() if name not in self.SOURCE_NAMES}
        renamed.update({self.SOURCE_NAMES[name]: value for name, value in table.items()
                        if name in self.SOURCE_NAMES})
        return renamed

    def _invisible(self, builder, props):
        """真源里**不着色**的那几张 —— 反壳描边的壳、只写模板的代理、把面部阴影投到脖子上的
        那张面片 —— 认领下来,交出一张完全透明的材质。

        它们在游戏里靠一整套渲染状态存在,本身没有表面可画。以前这里返回 None(「不认领」),
        于是它们掉回宿主的 Principled，变成一块**不透明的灰**；而这些代理面片就贴在角色身上，
        脸前面那张把整张脸盖没了（实测莫宁的 Face_FS 就是这样）。「认得出」和「画成灰块」
        是两回事：认得出就得负责说它不可见。"""
        name = props.name or self.MATERIAL_NAME
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()
        output = tree.nodes.new('ShaderNodeOutputMaterial')
        clear = tree.nodes.new('ShaderNodeBsdfTransparent')
        tree.links.new(clear.outputs[0], output.inputs['Surface'])
        _frame_generated(tree)
        try:
            mat.surface_render_method = 'BLENDED'
        except (AttributeError, TypeError):
            pass
        mat.use_backface_culling = False
        mat['ruri_uber_stack'] = self.PANEL_KEY
        mat['ruri_uber_part'] = ''
        mat['ruri_uber_shader'] = self._shader_name(builder, props) or ''
        print('[ruri-uber] {0} 用 {1}(非着色代理),按不可见认领'.format(
            name, mat['ruri_uber_shader']), flush=True)
        return mat

    def provider(self, builder, props):
        resolved = self._variant(builder, props)
        if resolved is NON_SHADING:
            return self._invisible(builder, props)
        if resolved is None:
            return None
        part_name, _part_id = resolved
        name = props.name or self.MATERIAL_NAME
        # 认领(_variant)读的是材质**自己**的事实,所以吃原样的 props;从这里往下都是记录,一律先翻成图的词汇。
        images = self._projected(self._load_images(builder, props))
        floats = dict(self._projected(props.floats))
        # 材质关键字只认材质自己的关键字表:引擎按它选变体,检视面开关与它可以不一致。
        stated_keywords = set(getattr(props, 'keywords', ()) or ())
        for keyword in self.m.get('material_keywords') or ():
            floats[keyword] = 1.0 if keyword in stated_keywords else 0.0
        # 家族声明透明的 part 在记录里就记成透明:_SurfaceType == 1 才吃 alpha(gBuffer0.w 是 materialFlags
        # 不是不透明度,接错皮肤隐形)。
        if self.PART_META[part_name]['transparent']:
            floats['_SurfaceType'] = 1.0
        ref = props.shader_ref
        record = {
            'ruri_uber_part': part_name,
            'ruri_uber_images': dict(images),
            'ruri_uber_floats': {key: float(value) for key, value in floats.items()},
            'ruri_uber_colors': {key: [float(x) for x in value] for key, value in self._projected(props.colors).items()},
            'ruri_uber_st': {key: [float(x) for x in value] for key, value in self._projected(props.texture_st).items()},
            'ruri_uber_disabled_passes': list(getattr(props, 'disabled_passes', ())),
            # 这张材质的 shader 画哪几趟:引擎按 LightMode 标签认一趟(无标签的认 Name),材质按同一个词关它。
            'ruri_uber_shader_passes': [str(light_mode or pass_name)
                                        for pass_name, light_mode in getattr(props, 'shader_passes', ())],
            'ruri_uber_shader_guid': str(ref.get('guid', '')) if isinstance(ref, dict) else '',
            'ruri_uber_shader': self._shader_name(builder, props) or '',
        }
        mat = self._compile(name, record)
        # 同名旧材质只改名让位,不删:宿主缓存攥着数据块,删了 = 悬垂指针 ReferenceError。
        stale = bpy.data.materials.get(name)
        if stale is not None and stale is not mat:
            stale.name = name + '.old'
            mat.name = name
        return mat

    def _compile(self, name, record):
        """一张材质按它的记录编出来:模板拷贝 + 贴图 + 参数常量。导入与开文件走同一条 —— 记录是内容,编出来的
        树不是:它引用的模板组是插件数据,开文件时不在,所以每次开都照记录重编。混合、透明形态与剔除面都由记录里的
        材质值加当前产物的声明现算,不另记。"""
        part = record['ruri_uber_part']
        meta = self.PART_META[part]
        floats = dict(record['ruri_uber_floats'])
        images = {}
        for slot, image in record['ruri_uber_images'].items():
            # 记录里的图是图本身:记录是内容,它就是图的用户,存盘才把只有记录认得、这一形态又不采的图带上。
            if not isinstance(image, bpy.types.Image):
                raise RuntimeError('[ruri-uber] 材质 {0} 的记录在槽 {1} 绑的不是一张图({2}):图被删了,或是按名字记图的'
                                   '旧记录 —— 重新导入它'.format(name, slot, image))
            images[slot] = image
        blend = self._record_blend(meta, floats, name)
        if blend is None:
            opaque = floats.get('_SurfaceType', 0.0) < 0.5 and not meta['transparent']
        else:
            opaque = blend == (BLEND_ONE, BLEND_ZERO)
        mat, swapped = self.instantiate(name, part, images=images, opaque=opaque, blend=blend,
                                        cull=self._record_cull(meta, floats, name))
        bst = record['ruri_uber_st'].get(self.ST_SLOT) or [1.0, 1.0, 0.0, 0.0]
        for node in mat.node_tree.nodes:
            if node.label == self.ST_NODE:
                # 先比后写:多数材质就是模板那份单位变换,白写一次宿主就把整棵树重算一遍。
                scale = (float(bst[0]), float(bst[1]), 1.0)
                location = (float(bst[2]), float(bst[3]), 0.0)
                if tuple(node.inputs['Scale'].default_value) != scale:
                    node.inputs['Scale'].default_value = scale
                if tuple(node.inputs['Location'].default_value) != location:
                    node.inputs['Location'].default_value = location
        # 随绑定真图走的表列按这次绑上的图算(instantiate),压过记录里的旧值;其余颜色行照记录。
        colors = {key: [float(x) for x in value] for key, value in _mixed(record['ruri_uber_colors']).items()
                  if _image_row_slot(key) is None}
        colors.update(_mixed(mat.get('ruri_uber_colors')))
        for key, value in record.items():
            mat[key] = value
        mat['ruri_uber_colors'] = colors
        self._declare_shading(mat)
        self.apply_params(mat)
        print('[ruri-uber] {0}: shader={1} part={2} images={3}'.format(
            name, record['ruri_uber_shader'], part, swapped), flush=True)
        return mat

    def _compiled(self, mat):
        """这张材质是本会话照记录编过的:树里的模板组全是本会话取来的插件数据。开文件后它们一个都不在(组口是空的),
        从别的文件 append 进来的、旧文件存下的也都不是 —— 那些都要照记录重编。"""
        tree = mat.node_tree
        if tree is None:
            return False
        groups = [node.node_tree for node in tree.nodes if node.bl_idname == 'ShaderNodeGroup']
        return bool(groups) and all(group is not None and group.is_runtime_data for group in groups)

    def recompile(self, mat):
        """照记录重编一张材质:新编一张,把别处写在旧材质上的内容(基座骨名、来源键……)带过去;
        调用方拿新的整批顶替旧的。记录缺格 = 不是生成栈写下的材质,响亮拒绝,不猜。"""
        missing = [key for key in self.RECORD_KEYS if key not in mat]
        if missing:
            raise RuntimeError('[ruri-uber] 材质 {0} 的记录缺 {1}:重新导入它'.format(mat.name, missing))
        record = {key: _plain(mat[key]) for key in self.RECORD_KEYS}
        new = self._compile(mat.name, record)
        compiled = set(self.RECORD_KEYS) | set(self.SUPERSEDED_KEYS) | {
            'ruri_uber_stack', self.BLEND_KEY, self.SHADING_KEY}
        for key in mat.keys():
            if key not in compiled:
                new[key] = _plain(mat[key])
        return new

    def compile_all(self):
        """宿主 load pass 的第二步(先清完插件数据再调),也收从别的文件 append 进来的材质:本栈认领、还没在本会话
        编过的每张材质照记录重编,新编的顶替旧的(旧的全部用户换到新的上),旧的删掉、名字还给新的。link 进来的材质
        记录是库文件的内容、本文件从不写它:照它编成本会话的替身(插件数据,不落盘),用户交宿主指到替身上,
        存盘时宿主让文件照旧指着库里那张。返回新编的材质。"""
        claimed = [mat for mat in bpy.data.materials
                   if mat.get('ruri_uber_stack') == self.PANEL_KEY and mat.get('ruri_uber_part')
                   and mat.get(self.TEMPLATE_KEY) is None]
        pending = [mat for mat in claimed if mat.library is None and not self._compiled(mat)]
        linked = [mat for mat in claimed if mat.library is not None and not _host().twinned(mat)]
        if not pending and not linked:
            return []
        # 旧材质的死树不在这里清空:清空会给它打依赖图更新标记,紧接着整批删掉,下一拍的依赖图更新里就留着一个
        # 原件已释放的条目,读它 .original 的处理器当场崩(实测)。
        replaced = {}
        twins = []
        failed = []
        self._probes = []
        try:
            for mat in pending:
                try:
                    new = self.recompile(mat)
                except Exception as exc:
                    failed.append('{0}: {1}'.format(mat.name, exc))
                    continue
                # 编好一张换一张:这一刻文件里的大树只有已经编好的那些,换用户那一遍走得最短。
                mat.user_remap(new)
                replaced[mat] = new
            for mat in linked:
                try:
                    twin = _host().plugin_data(self.recompile(mat))
                except Exception as exc:
                    failed.append('{0}: {1}'.format(mat.name_full, exc))
                    continue
                twins.append((mat, twin))
        finally:
            probes, self._probes = self._probes, None
        _retire_materials(replaced, probes)
        _host().adopt_twins(twins)
        if twins:
            print('[ruri-uber] {0} 张 link 进来的材质照库里的记录编成替身(插件数据,不落盘)'.format(len(twins)), flush=True)
        if failed:
            raise RuntimeError('[ruri-uber] {0} 张材质照记录编不出来:{1}'.format(len(failed), failed))
        return list(replaced.values()) + [twin for _mat, twin in twins]

    def rewire_capabilities(self, mat):
        """重接兑现面(换灯/换世界后):只回收 ruri_cap 标记的节点,图本体与参数一概不碰。"""
        part = mat.get('ruri_uber_part')
        if part is None or part not in self.m['parts'] or mat.node_tree is None:
            return False
        if mat.get('ruri_uber_stack') != self.PANEL_KEY:
            return False
        if not self._compiled(mat):
            # 兑现面是编出来的图的一部分:本会话还没照记录编过的图(刚开的文件、刚 append 进来的)没有可重接的,编它时按当下的场景接。
            return False
        spec = self.part(part)
        nt = mat.node_tree
        # 重接期间把出口摘下来,收尾再接回:宿主的灯链接校验在每次改链时跑,并且用上一次的判定
        # 决定这次遍历走哪些链接;闭包还没重建时灯节点经 alpha 够得到出口却够不到 Light Accumulation,
        # 那一拍判非法后会一直留在树里(codegen 对非法入链的节点 need_exec=0,材质丢主光)。
        # 出口不接,途中没有任何东西够得到出口,收尾那一次校验与建图时完全一样。
        output = next((n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial'), None)
        surface_from = None
        if output is not None and output.inputs['Surface'].is_linked:
            surface_from = output.inputs['Surface'].links[0].from_socket
            nt.links.remove(output.inputs['Surface'].links[0])
        for stale in [n for n in nt.nodes if n.get('ruri_cap') is not None]:
            nt.nodes.remove(stale)
        ordered = sorted((n for n in nt.nodes if n.get('ruri_inst') is not None),
                         key=lambda n: int(n['ruri_inst']))
        if not ordered:
            raise RuntimeError('[ruri-cap] 材质 {0} 无实例序标记(旧产物),请重新导入'.format(mat.name))
        g = G(nt, is_group=False)
        ctx = {'material': mat}
        for row in spec['capabilities']:
            self._wire_capability(g, ordered, row, ctx)
        # 体内割点(灯住这儿):按 (zone, 序) 找回体实例链再接,漏掉 = 循环里的兑现面永不回来。
        for zone in spec['zones']:
            if not zone['capabilities']:
                continue
            binsts = sorted((n for n in nt.nodes if n.get('ruri_zone') == zone['sock']),
                            key=lambda n: int(n['ruri_binst']))
            if not binsts:
                raise RuntimeError('[ruri-cap] 材质 {0} 的循环 {1} 无体实例标记,请重新导入'.format(
                    mat.name, zone['sock']))
            for row in zone['capabilities']:
                self._wire_capability(g, binsts, row, ctx)
        # 乘法帧没有闭包(出口就是一枚带颜色的 Transparent BSDF,见 build_material),没什么可重接的。
        if self._stored_blend(mat) not in MULTIPLY_BLENDS:
            seat = next((n for n in nt.nodes if n.label == self.SURFACE_MIX_LABEL), None)
            coverage = next((n for n in nt.nodes if n.label == self.SURFACE_COVERAGE_LABEL), None)
            if seat is None or seat.get(self.CLOSURE_SEAT_KEY) is None or coverage is None:
                raise RuntimeError('[ruri-cap] 材质 {0} 无表面混合/覆盖率标记(旧产物),请重新导入'.format(mat.name))
            g._set(seat.inputs[int(seat[self.CLOSURE_SEAT_KEY])], self._wire_closure(
                g, mat, ordered, spec['finals'], self._final_socket(ordered, spec['finals'], 'ret_gBuffer0_w'),
                coverage.outputs[0]))
        if surface_from is not None:
            nt.links.new(surface_from, output.inputs['Surface'])
        g.layout()
        self._require_budget(mat)
        # 纯数据写不触发依赖图,必须自己打脏标记,否则 EEVEE 继续用旧编译结果。
        nt.update_tag()
        mat.update_tag()
        return True

    # ==================== 顶点腿(几何节点) ====================

    def _material_images(self, mat):
        """顶点树换图真源:①记录里的全量图(含只有顶点腿消费的槽);②材质树兜底。"""
        images = {}
        for slot, img in dict(mat.get('ruri_uber_images') or {}).items():
            if img is not None:
                images[slot] = img

        def walk(tree, depth=0):
            if depth > 4 or tree is None:
                return
            for nd in tree.nodes:
                if nd.type == 'TEX_IMAGE' and nd.image is not None:
                    images.setdefault(_slot_of(nd.label or nd.name or nd.image.name), nd.image)
                elif nd.type == 'GROUP':
                    walk(nd.node_tree, depth + 1)
        if mat.node_tree is not None:
            walk(mat.node_tree)
        return images

    @staticmethod
    def _mat_meta(mat):
        floats = dict(mat.get('ruri_uber_floats') or {})
        st = _mixed(mat.get('ruri_uber_st'))
        colors = _mixed(mat.get('ruri_uber_colors'))
        return floats, st, colors

    def _fill_uniform_sockets(self, node, floats, st, colors):
        """raw 直灌顶点组实例:socket 名 = 游戏属性名(V4 = vec+_w 对)。
        sRGB 属性在这里也补线性化 —— 顶点腿与片元腿读的是同一个 uniform,两条腿不许各说各话。"""

        def cvt(name, value):
            return _srgb_to_linear(value) if name in self.SRGB_PARAMS else float(value)

        for sock in node.inputs:
            name = sock.name
            if name.endswith('_w'):
                base = name[:-2]
                if base.endswith('_ST') and base[:-3] in st:
                    sock.default_value = float(st[base[:-3]][3])
                elif base in colors:
                    sock.default_value = float(colors[base][3])
                continue
            if name.endswith('_ST') and name[:-3] in st:
                v = st[name[:-3]]
                sock.default_value = (float(v[0]), float(v[1]), float(v[2]))
            elif name in colors:
                v = colors[name]
                sock.default_value = (cvt(name, v[0]), cvt(name, v[1]), cvt(name, v[2]))
            elif name in floats:
                try:
                    sock.default_value = cvt(name, floats[name])
                except (TypeError, ValueError):
                    pass

    def _rig_basis_armature(self, obj):
        if not self.RIG.get('bone'):
            return None
        arm = next((m.object for m in obj.modifiers
                    if m.type == 'ARMATURE' and m.object is not None), None)
        if arm is None and obj.parent is not None and obj.parent.type == 'ARMATURE':
            arm = obj.parent
        return arm

    def rig_bone_of(self, mat):
        """这张材质的基座骨,说的是 **Unity 骨名**。运行期唯一真源 = 材质自己的键
        (instantiate 按清单播种,面板改的也是它);清单值只是种子,不在这里当兜底读。"""
        return str(mat.get(self.RIG.get('prop') or '') or '')

    def _rig_identity_api(self, key):
        """宿主那份骨骼身份 API(None = 配方没声明)。身份编码只有宿主一个读者,
        生成物经这一个口子问它,不自己再解一遍那份编码。"""
        import importlib
        module = self.host.get('rig_identity_module') or ''
        fn = self.host.get(key) or ''
        if not module or not fn:
            return None
        return getattr(importlib.import_module(module), fn)

    def rig_resolve_bone(self, arm, unity_name):
        """Unity 骨名 → 本 rig 当下的 Blender 骨名('' = 这副骨架没有这根)。
        身份烙在骨上、改名碰不到,所以这条翻译在改过名的 rig 上照样成立。"""
        api = self._rig_identity_api('rig_bone_fn')
        if api is None or arm is None or not unity_name:
            return ''
        return api(arm, unity_name) or ''

    def rig_apply(self, mat, arm, obj):
        """基座接到材质:三列读**对象自定义属性**,由 push_rig_basis 每帧写。

        为什么不是驱动器:驱动器写 shader socket = 每帧弄脏整棵材质树,实测视口
        **468.7ms/帧(2.1fps) vs 52.7ms(19fps),8.89 倍**。对象自定义属性走逐对象 UBO,
        不碰材质树,实测与静态基线同档(0.80x)。这与「handler 推 socket 不行」是同一条纪律。
        为什么不是几何点属性:那会把 uniform 塞进几何管线,描边一关基座就跟着没(本次根因)。
        接不上就说出来,socket 留在组缺省 = 单位阵。"""
        tree = mat.node_tree
        if tree is None:
            return False
        instances = [grp for grp in self._panel_insts(mat)
                     if grp.inputs.get(RIG_BASIS_SOCKETS[0]) is not None]
        if not instances:
            return False
        unity_bone = self.rig_bone_of(mat)
        bone_name = self.rig_resolve_bone(arm, unity_bone)
        if arm is None or not bone_name:
            print('[ruri-rig] !! {0}: 基座接不上(骨架={1} 记的骨={2} 解析={3})—— 留在绑定姿势'
                  .format(mat.name, getattr(arm, 'name', None), unity_bone or '(空)',
                          bone_name or '(这副骨架没有)'), flush=True)
            return False
        self._rig_clear(mat)
        g = G(tree, is_group=False)
        for index in range(3):
            node = g._nd('ShaderNodeAttribute')
            node.attribute_type = 'OBJECT'
            node.attribute_name = '["{0}{1}"]'.format(RIG_OBJECT_PROP, index)
            node.label = RIG_ATTR_LABEL + str(index)
            for grp in instances:
                sock = grp.inputs.get(RIG_BASIS_SOCKETS[index])
                if sock is not None:
                    tree.links.new(node.outputs['Vector'], sock)
        _frame_generated(tree)
        RIG_DRIVEN[obj.name] = (arm.name, bone_name)
        return True

    def _rig_clear(self, mat):
        """幂等:本桥在这张材质上留下的一切先撤干净再接。

        判据放宽到 `RuriRig` 前缀(不只当前那批标签):这座桥换过送值通道,旧通道留下的
        节点与**挂在它们 socket 上的驱动器**必须一起走 —— 驱动器是每帧弄脏材质树的那个
        8.89 倍,漏掉一张材质就等于漏掉整帧。删节点会连带删掉它 socket 上的驱动器。"""
        tree = mat.node_tree
        for node in [n for n in tree.nodes if (n.label or '').startswith('RuriRig')]:
            tree.nodes.remove(node)
        animation = tree.animation_data
        if animation is not None and not len(animation.drivers):
            tree.animation_data_clear()

    def rig_rescan(self):
        """重建 push 工作单。**重开文件后它是空的** —— 工作单是进程态,而 .blend 里
        只存着上次写下的属性值;没人重建就等于基座冻结在存盘那一刻(静默)。
        所以 load_post 必须叫一次,判据从场上的材质真值现算,不依赖任何记忆。"""
        rig_parts = set(self.RIG.get('parts') or [])
        if not rig_parts:
            return 0
        found = 0
        for obj in bpy.context.scene.objects:
            if obj.type != 'MESH' or obj.data is None:
                continue
            mats = [slot.material for slot in obj.material_slots
                    if slot.material is not None and slot.material.get('ruri_uber_stack') == self.PANEL_KEY
                    and slot.material.get('ruri_uber_part') in rig_parts]
            if not mats:
                continue
            arm = self._rig_basis_armature(obj)
            bone_name = self.rig_resolve_bone(arm, self.rig_bone_of(mats[0]))
            if arm is not None and bone_name:
                RIG_DRIVEN[obj.name] = (arm.name, bone_name)
                RIG_SCANNED[0] = True
                found += 1
        return found

    @staticmethod
    @bpy.app.handlers.persistent
    def push_rig_basis(*_args):
        """把基座骨的世界增量 W = A·Pose·Rest⁻¹·A⁻¹(A = 骨架世界矩阵)的三列写到对象上。**逐对象 UBO,不碰材质树**。

        值没变就不写 —— 写属性会给对象打脏标记,在 depsgraph handler 里无条件写会自激。
        列在真源世界系里(声明的世界基 B:宿主 = B·真源,真源增量 = Bᵀ·W·B),与 UNITY_MATRIX_M 的列语义同源。
        增量必须先在世界系里取再换轴:在骨架空间里取、按固定轴序换,只在骨架恰好保持导入那一转偏航时成立,
        骨架在场景里转了向,头的俯仰与侧倾就被换到别的轴上。"""
        import mathutils
        if not RIG_SCANNED[0]:
            RIG_SCANNED[0] = True
            for stack in STACKS:
                if stack.post is None and (stack.RIG.get('parts') or []):
                    stack.rig_rescan()
        if not RIG_DRIVEN:
            return
        # 🔴 姿势解算的结果住在**求值副本**上;原始数据块的 pose_bone.matrix 只有在有人
        # 调过 view_layer.update() 之后才被刷回。在 depsgraph handler 里读原始副本 =
        # 读到上一次的姿势 ⇒ 载入时看着对(那一拍刚好是文件里的姿势),交互拖骨之后
        # 基座就再也不动 —— 表现成"坐标系不实时更新"。所以这里必须走 depsgraph。
        depsgraph = next((a for a in _args if isinstance(a, bpy.types.Depsgraph)), None)
        if depsgraph is None:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        basis = mathutils.Matrix(_world_basis())
        for obj_name, (arm_name, bone_name) in list(RIG_DRIVEN.items()):
            obj = bpy.data.objects.get(obj_name)
            arm = bpy.data.objects.get(arm_name)
            if obj is None or arm is None:
                RIG_DRIVEN.pop(obj_name, None)
                continue
            evaluated_armature = arm.evaluated_get(depsgraph)
            pose = evaluated_armature.pose.bones.get(bone_name)
            if pose is None:
                continue
            armature_world = evaluated_armature.matrix_world.to_3x3()
            delta = (armature_world @ pose.matrix.to_3x3() @ pose.bone.matrix_local.to_3x3().inverted()
                     @ armature_world.inverted())
            rotation = basis.transposed() @ delta @ basis
            # 🔴 着色器读的是**求值副本**上的属性。本 handler 跑在 depsgraph_update_post,
            # 那一拍的求值副本已经建好了 —— 只写原始副本 = 画面永远慢一拍;而"值没变就不写"
            # 的守卫又让它不再重新打脏标记,于是**永远追不上**(实测求值副本差到 1.99)。
            # 所以本帧绘制读哪份就写哪份:求值副本每次都写,原始副本只在变了时写(它是
            # 存盘种子,也是下一次求值的来源;无条件写会打脏标记自激)。
            evaluated_object = obj.evaluated_get(depsgraph)
            for index in range(3):
                value = tuple(rotation.col[index].normalized())
                key = RIG_OBJECT_PROP + str(index)
                try:
                    evaluated_object[key] = value
                except Exception:
                    pass
                previous = obj.get(key)
                if previous is None or max(abs(previous[k] - value[k]) for k in range(3)) > 1e-6:
                    obj[key] = value

    # socket 数据类型 → 组接口 socket 类型(Blender 的类型对照,不是内容词汇)
    VTX_INTERFACE_SOCKET = {'VALUE': 'NodeSocketFloat', 'INT': 'NodeSocketInt',
                            'BOOLEAN': 'NodeSocketBool', 'VECTOR': 'NodeSocketVector',
                            'RGBA': 'NodeSocketColor', 'IMAGE': 'NodeSocketImage'}

    @staticmethod
    def _vtx_same(a, b):
        """两个 socket 值等不等。数据块比身份,数比值 —— 只有它说「不等」才算用户动过。"""
        if isinstance(a, bpy.types.ID) or isinstance(b, bpy.types.ID) or a is None or b is None:
            return a is b
        if hasattr(a, '__len__') != hasattr(b, '__len__'):
            return False
        if hasattr(a, '__len__'):
            va, vb = list(a), list(b)
            return len(va) == len(vb) and all(abs(float(x) - float(y)) <= 1e-6
                                              for x, y in zip(va, vb))
        return abs(float(a) - float(b)) <= 1e-6

    def _vtx_knob_rows(self):
        """顶点腿上哪些 socket 是旋钮:问参数面。它是「能拧什么」的唯一声明(生成自
        [ShaderProperty]),顶点腿不过是同一批 uniform 的另一条消费腿,不在宿主侧另立名单。
        贴图行按它的 _ST 认;余下的(varying / 视图基轴 / 屏幕尺寸 / 管线常量)不是旋钮。"""
        rows = {}
        for row in self._panel_rows():
            rows[row['name']] = row
            if row['kind'] == 'TEXTURE':
                rows[row['name'] + '_ST'] = row
        return rows

    def _vtx_expected(self, mat, template_name, name, images=None):
        """这一格「材质当下说的值」。**材质快照是唯一真源**:片元腿、顶点腿、修改器面板
        三个消费面都从它派生,所以播种与「用户动过没有」的判据共用这一个函数,不可能各说各话。
        快照没声明的落模板接口缺省(codegen 定的那个;贴图槽即中性占位图)。"""
        floats, st, colors = self._mat_meta(mat)
        if name.endswith('_ST') or name.endswith('_ST_w'):
            tail = name.endswith('_ST_w')
            value = st.get(name[:-5] if tail else name[:-3])
            if value is not None:
                return float(value[3]) if tail else (float(value[0]), float(value[1]), float(value[2]))
        elif name.endswith('_w'):
            value = colors.get(name[:-2])
            if value is not None:
                return float(value[3])
        elif name in colors:
            srgb = name in self.SRGB_PARAMS
            return tuple(_srgb_to_linear(v) if srgb else float(v) for v in list(colors[name])[0:3])
        elif name in floats:
            value = float(floats[name])
            return _srgb_to_linear(value) if name in self.SRGB_PARAMS else value
        else:
            image = (self._material_images(mat) if images is None else images).get(name)
            if image is not None:
                return image
        return self._vtx_template_default(template_name, name)

    def _vtx_template_default(self, template_name, name):
        """模板接口上那一格的缺省 —— codegen 说了算的那个值。"""
        if not template_name:
            return None
        for item in self.group(template_name).interface.items_tree:
            if item.item_type != 'PANEL' and item.in_out == 'INPUT' and item.name == name:
                value = getattr(item, 'default_value', None)
                return tuple(value) if hasattr(value, '__len__') and not isinstance(value, str) else value
        return None

    @staticmethod
    def _vtx_own_samplers(node, images):
        """顶点模板里「贴图自带采样器」那几个槽的逐实例口:按这一格绑定的图自己陈述的寻址与过滤灌
        (模板共享,状态随图走,见物化脚本 own_sampler_fetch)。images 没提到的槽不动;提到而为 None
        (清空回落中性占位)= 占位图自己的状态。"""
        for sock in node.inputs:
            for axis, suffix in enumerate(OWN_SAMPLER_SOCKETS):
                if not sock.name.endswith(suffix) or sock.name[:-len(suffix)] not in images:
                    continue
                wrap_u, wrap_v, point = _own_sampler_state(images[sock.name[:-len(suffix)]])
                value = (OWN_SAMPLER_WRAPS.index(wrap_u), OWN_SAMPLER_WRAPS.index(wrap_v), int(point))[axis]
                if sock.default_value != value:
                    sock.default_value = value

    def _vtx_expose(self, tree, group_input, node, mat, knobs, panels, template_name, state,
                    images):
        """把这个组实例上的材质旋钮抬到**修改器接口**,一材质一段。

        为什么不能就留在组实例的 socket 缺省上:这棵树是幂等换血重建的(相机一动、对象一进场
        都会重建,见 derived_state 的 vertex 级),在节点编辑器里拧的值下一拍就没。
        抬到接口后值住在修改器实例上;而**值本身仍然只有材质一处真源** —— 播种从材质来,
        在面板上拧动由 sync_vertex_knobs 写回材质,两个方向都过 panel_write*。"""
        for sock in node.inputs:
            if sock.is_linked:
                continue
            socket_type = self.VTX_INTERFACE_SOCKET.get(sock.type)
            row = knobs.get(sock.name[:-2] if sock.name.endswith('_w') else sock.name)
            if socket_type is None or row is None:
                continue
            panel = panels.get(mat.name)
            if panel is None:
                panel = tree.interface.new_panel(mat.name)
                panels[mat.name] = panel
            item = tree.interface.new_socket(name=sock.name, in_out='INPUT',
                                             socket_type=socket_type, parent=panel)
            item.description = row['label']
            if row['kind'] == 'SLIDER' and sock.type == 'VALUE' and not sock.name.endswith('_w'):
                item.min_value = float(row['min'])
                item.max_value = float(row['max'])
            value = self._vtx_expected(mat, template_name, sock.name, images)
            try:
                item.default_value = value
            except (TypeError, ValueError):
                pass
            key = mat.name + '|' + sock.name
            state['keys'][item.identifier] = key
            state['templates'][key] = template_name
            state['values'][item.identifier] = value
            wire = next((o for o in group_input.outputs if o.identifier == item.identifier), None)
            if wire is not None:
                tree.links.new(wire, sock)

    def _vtx_panel_write(self, mat, name, value):
        """材质改了一个 uniform → 推到全部修改器上那一格。旋钮抬到接口之后,写组实例的 socket
        缺省是**无效**的(那一格被组输入占线),值住在修改器实例上。
        推给**所有**对象:同一张材质挂在几个对象上时,它们说的必须是同一件事。
        值没变就不写 —— 脚本写端口要 update_tag,无条件写会在 depsgraph handler 里自激。"""
        key = mat.name + '|' + name
        for obj in bpy.data.objects:
            mod = obj.modifiers.get(self.VTX_MODIFIER)
            tree = getattr(mod, 'node_group', None) if mod is not None else None
            if tree is None:
                continue
            written = False
            for identifier, mapped in dict(tree.get('ruri_vtx_keys') or {}).items():
                if str(mapped) != key:
                    continue
                port = getattr(mod.properties.inputs, str(identifier), None)
                if port is None or self._vtx_same(port.value, value):
                    continue
                try:
                    port.value = value
                except (TypeError, ValueError):
                    continue
                written = True
            if written:
                obj.update_tag()

    @staticmethod
    @bpy.app.handlers.persistent
    def sync_vertex_knobs(_scene, depsgraph):
        """修改器面板上拧动的值 → 写回材质。

        为什么必须写回而不是就地留着:顶点腿与片元腿读的是**同一批 uniform**。值只留在修改器上,
        同一张 `_FurDirMap` 就会「壳长变了而毛发遮罩没变」,同一材质挂两个对象也永远对不上 ——
        两条都是画面上看得见、日志里一声不响的错。Blender 不给修改器输入改动回调,所以只能在
        依赖图落定后按材质真值比对;相等就什么都不做(无条件写会自激)。"""
        for stack in STACKS:
            if stack.post is None:
                stack.vtx_writeback(depsgraph)

    @staticmethod
    def _vtx_plain(value):
        """端口值的可比形态:数据块按名字,向量按元组,标量原样。"""
        if isinstance(value, bpy.types.ID):
            return value.name_full
        if hasattr(value, '__len__') and not isinstance(value, str):
            return tuple(value)
        return value

    def vtx_writeback(self, depsgraph):
        # 这条挂在 depsgraph_update_post 上,每一拍都必须便宜:播放动画、推相机时几何每拍都在重算,而拧
        # 旋钮是偶发的。所以只看**这一拍被更新**的对象,每个端口只在值与上一次见到的不同(= 有人拧过)
        # 时才去和材质真值比;参数面 600 行的字典与材质图走查都推迟到真有一格被拧过之后。
        rows = None
        seen = self._vtx_seen
        for update in depsgraph.updates:
            if not isinstance(update.id, bpy.types.Object):
                continue
            obj = update.id.original
            mod = obj.modifiers.get(self.VTX_MODIFIER)
            tree = getattr(mod, 'node_group', None) if mod is not None else None
            if tree is None:
                continue
            keys = {str(k): str(v) for k, v in dict(tree.get('ruri_vtx_keys') or {}).items()}
            if not keys:
                continue
            inputs = mod.properties.inputs
            port_of = {}
            dialed = []
            for identifier, key in keys.items():
                port = getattr(inputs, identifier, None)
                if port is None:
                    continue
                port_of[key] = port
                value = self._vtx_plain(port.value)
                marker = (obj.session_uid, identifier)
                if seen.get(marker) == value:
                    continue
                seen[marker] = value
                dialed.append(key)
            if not dialed:
                continue
            if rows is None:
                rows = {row['name']: row for row in self._panel_rows()}
            templates = {str(k): str(v) for k, v in dict(tree.get('ruri_vtx_templates') or {}).items()}
            images_of = {}
            for key in dialed:
                port = port_of[key]
                mat_name, _sep, prop = key.partition('|')
                mat = bpy.data.materials.get(mat_name)
                if mat is None or not self.panel_claims(mat):
                    continue
                if mat_name not in images_of:
                    images_of[mat_name] = self._material_images(mat)
                if self._vtx_same(port.value, self._vtx_expected(
                        mat, templates.get(key), prop, images_of[mat_name])):
                    continue
                self._vtx_commit(mat, rows, prop, port_of)

    def _vtx_commit(self, mat, rows, prop, port_of):
        """一格被拧动 → 经材质面板那三条写路落回材质。走 panel_write* 而不是自己写快照:
        sRGB 还原、_ST 的三个消费面、换图时的中性图回落与两通道布局都住在那三个函数里,
        绕过它们就是在宿主侧造第二处真源。"""
        def value_of(name):
            port = port_of.get(mat.name + '|' + name)
            return None if port is None else port.value

        if prop.endswith('_ST') or prop.endswith('_ST_w'):
            slot = prop[:-5] if prop.endswith('_ST_w') else prop[:-3]
            row = rows.get(slot)
            vector = value_of(slot + '_ST')
            if row is None or vector is None:
                return
            tail = value_of(slot + '_ST_w')
            self.panel_write_st(mat, row, (float(vector[0]), float(vector[1])),
                                (float(vector[2]), 0.0 if tail is None else float(tail)))
            return
        row = rows.get(prop)
        if row is not None and row['kind'] == 'TEXTURE':
            self.panel_write_image(mat, row, value_of(prop))
            return
        base = prop[:-2] if prop.endswith('_w') and prop[:-2] in rows else prop
        row = rows.get(base)
        if row is None:
            return
        srgb = base in self.SRGB_PARAMS

        def authored(v):
            return _linear_to_srgb(float(v)) if srgb else float(v)

        if row['kind'] in ('SWITCH', 'VALUE', 'SLIDER', 'INT'):
            scalar = value_of(base)
            if scalar is not None:
                self.panel_write(mat, row, authored(scalar))
            return
        vector = value_of(base)
        if vector is None:
            return
        tail = value_of(base + '_w')
        self.panel_write(mat, row, [authored(vector[0]), authored(vector[1]), authored(vector[2]),
                                    1.0 if tail is None else float(tail)])

    @staticmethod
    @bpy.app.handlers.persistent
    def follow_material_records(_scene, depsgraph):
        """材质记录里换了图、改了值 → 编好的树与顶点腿跟上。

        记录是内容,树照记录编:开文件时照记录重编,会话中途改记录的(别的插件把拉回来的贴图换进
        槽里、把别处调的参数写回来,脚本)只要给材质打 update_tag,依赖图那一拍就带着它。不跟上的代价
        是画面停在旧图旧值上、存盘重开才突然变 —— 两边说的不是同一张材质,而且一声不响。"""
        for stack in STACKS:
            if stack.post is None:
                stack.follow_record_images(depsgraph)
                stack.follow_record_values(depsgraph)

    def follow_record_values(self, depsgraph):
        # 每一拍都必须便宜:只看这一拍被更新、本栈认领的材质;记录与上次落树的那一份相同就什么都不做。
        # 没有那一份 = 这个运行时没给它落过树(插件重载),此刻的树就是照记录编的,记下即可。
        for update in depsgraph.updates:
            if not isinstance(update.id, bpy.types.Material):
                continue
            mat = update.id.original
            if mat.node_tree is None or not self.panel_claims(mat):
                continue
            applied = self._applied_values.get(mat.session_uid)
            recorded = self._record_values(mat)
            if applied is None:
                self._applied_values[mat.session_uid] = recorded
            elif applied != recorded:
                self.apply_record_values(mat, applied, recorded)

    def apply_record_values(self, mat, applied, recorded):
        """记录里被整行改写的值 → 顶点腿修改器口 + 平铺节点 + 组实例常量,只推变了的那些,与 panel_write*
        同一套线性化。面板逐格拧走 panel_write*;别的插件或脚本整行改写记录时走这一条。"""
        rows = {row['name']: row for row in self._panel_rows()}
        for name, value in recorded['floats'].items():
            if applied['floats'].get(name) == value:
                continue
            scalar = float(value)
            self._vtx_panel_write(mat, name, _srgb_to_linear(scalar) if name in self.SRGB_PARAMS else scalar)
        for name, value in recorded['colors'].items():
            if applied['colors'].get(name) == value:
                continue
            vec4 = ([float(v) for v in value] + [0.0] * 4)[:4]
            xyz = ((_srgb_to_linear(vec4[0]), _srgb_to_linear(vec4[1]), _srgb_to_linear(vec4[2]))
                   if name in self.SRGB_PARAMS else (vec4[0], vec4[1], vec4[2]))
            self._vtx_panel_write(mat, name, xyz)
            row = rows.get(name)
            if row is not None and row['size'] >= 4:
                self._vtx_panel_write(mat, name + '_w', vec4[3])
        for name, value in recorded['st'].items():
            if applied['st'].get(name) == value:
                continue
            st_value = ([float(v) for v in value] + [0.0] * 4)[:4]
            self._vtx_panel_write(mat, name + '_ST', (st_value[0], st_value[1], st_value[2]))
            self._vtx_panel_write(mat, name + '_ST_w', st_value[3])
            row = rows.get(name)
            if row is not None and row.get('st_node') and mat.node_tree is not None:
                for node in mat.node_tree.nodes:
                    if node.label == row['st_node']:
                        node.inputs['Scale'].default_value = (st_value[0], st_value[1], 1.0)
                        node.inputs['Location'].default_value = (st_value[2], st_value[3], 0.0)
        self.apply_params(mat)

    def follow_record_images(self, depsgraph):
        # 每一拍都必须便宜:只看这一拍被更新的材质。只比树里已有图节点的槽 —— 这一形态不采的槽写了也接
        # 不上,拿它比就是每拍都「不等」、每拍都写、自激;相等不写,换图本身会打脏标记。
        rows = None
        for update in depsgraph.updates:
            if not isinstance(update.id, bpy.types.Material):
                continue
            mat = update.id.original
            if mat.node_tree is None or not self.panel_claims(mat):
                continue
            recorded = dict(mat.get('ruri_uber_images') or {})
            if not recorded:
                continue
            wired = {}
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE':
                    wired.setdefault(_slot_of(node.label or ''), node.image)
            stale = [slot for slot, image in recorded.items()
                     if isinstance(image, bpy.types.Image) and slot in wired and wired[slot] != image]
            if not stale:
                continue
            if rows is None:
                rows = {row['name']: row for row in self._panel_rows()}
            for slot in stale:
                row = rows.get(slot)
                if row is not None and row['kind'] == 'TEXTURE':
                    self.panel_write_image(mat, row, recorded[slot])

    def _stack_slots(self, obj):
        """这个对象上属于本栈的材质槽 (槽号, 材质),按槽上**实际生效**的材质认:挂在对象层的材质(库覆盖的
        网格数据换不了材质,只能挂对象层)与数据层的一样算。描边克隆不算 —— 它是产物不是输入。"""
        return [(i, slot.material) for i, slot in enumerate(obj.material_slots)
                if slot.material is not None and slot.material.get('ruri_uber_part') in self.KNOWN_PARTS
                and slot.material.get('ruri_uber_stack') == self.PANEL_KEY
                and not slot.material.get('ruri_outline_clone')]

    def _asks(self, part, capability):
        """这个 part 的片元(连同循环体)有没有这条环境询问 —— 按完整模板认,它是各签名的超集。"""
        spec = self.m['parts'][part]
        rows = list(spec['capabilities']) + [row for zone in spec['zones'] for row in zone['capabilities']]
        return any(row['cap'] == capability for row in rows)

    @staticmethod
    def _screen_size(scene):
        """真源 _ScreenParams 的前两格:真实输出像素(分辨率 × 百分比)。"""
        scale = scene.render.resolution_percentage / 100.0
        return {SCREEN_SOCKETS[0]: float(scene.render.resolution_x) * scale,
                SCREEN_SOCKETS[1]: float(scene.render.resolution_y) * scale}

    def _write_screen_sockets(self, node, screen):
        """把输出像素数灌进一个组实例,返回有没有真写。**按 socket 在不在决定写不写** ——
        组接口是图自己决定的,按名硬索引会在没有那一格的组上 KeyError。值没变不写:
        这条也跑在重灌路径上,无条件写会打依赖图脏标记然后自激。"""
        written = False
        for sock in node.inputs:
            if sock.name not in SCREEN_SOCKETS:
                continue
            value = float(screen[sock.name])
            if self._vtx_same(sock.default_value, value):
                continue
            sock.default_value = value
            written = True
        return written

    def push_screen_size(self, objects=None, camera=None):
        """输出设置或相机变了 → 只把像素数重灌进**已经存在**的顶点树(相机本身树里现读,camera 不用)。

        它不建树、不建修改器,更不按材质现值重判一次描边 —— 那个判断只属于从游戏导入的那一刻。
        按现值实时重判的代价是用户删掉的修改器会在下一次推镜头时自己长回来,而画面上没有
        任何东西说明是谁加的。"""
        screen = self._screen_size(bpy.context.scene)
        done = 0
        for obj in (objects if objects is not None else bpy.data.objects):
            mod = obj.modifiers.get(self.VTX_MODIFIER)
            tree = getattr(mod, 'node_group', None) if mod is not None else None
            if tree is None:
                continue
            touched = False
            for node in tree.nodes:
                if node.get('ruri_vtx_mat') is None:
                    continue
                if self._write_screen_sockets(node, screen):
                    touched = True
            if touched:
                obj.update_tag()
                done += 1
        return done

    @staticmethod
    def _drawn_into_views(obj):
        """对象会不会出现在任何一幅画面里:相机直视、漫反射、镜面、透射、体积散射,任何一种可见就算。"""
        return (obj.visible_camera or obj.visible_diffuse or obj.visible_glossy
                or obj.visible_transmission or obj.visible_volume_scatter)

    def apply_rig_basis(self, objects=None):
        """脸部基座:把**当下骨名**接进材质,再把值推一次。只碰材质节点与对象自定义属性,
        一个几何节点都不碰(rig_apply 的 docstring 写着为什么基座不是几何点属性)—— 所以
        骨改个名不该让谁凭空长出一个修改器。"""
        rig_parts = set(self.RIG.get('parts') or [])
        if not rig_parts:
            return 0
        done = 0
        for obj in (objects if objects is not None else bpy.context.scene.objects):
            if obj.type != 'MESH' or obj.data is None:
                continue
            mats = [m for _slot, m in self._stack_slots(obj) if m['ruri_uber_part'] in rig_parts]
            if not mats:
                continue
            arm = self._rig_basis_armature(obj)
            for mat in mats:
                self.rig_apply(mat, arm, obj)
            done += 1
        # 基座的**值**推一次。接线刚在上面做完,但值一直只由依赖图 / 帧变化两个 handler 推
        # —— 那两个在 --background 里一次都不响,于是脸上的 UNITY_MATRIX_M 列全读到
        # 「属性不存在」= 0,头部朝向塌成零向量,SDF 取样整个错位,脸就是黑的。
        self.push_rig_basis()
        return done

    def apply_vertex_stage(self, objects, camera=None):
        """壳层位移 + 反壳描边的壳(同树虚拟几何;壳往外挪多少在材质的位移出口里逐视图算,见 _wire_outline)。
        **唯一的生成途径 = 从游戏读材质、造出这批对象的那一刻**:objects 只能是导入刚造出来的对象,没有「整场景」、
        没有开文件重填、没有手动重建 —— 拓扑的判据(这张材质有没有描边 pass、宽度是不是 0)说的是导入那一刻的游戏真值,
        之后任何时候按材质现值重判都是凭空长修改器。树是插件数据不进 .blend,所以重开文件后修改器是空的,要壳与描边就重新导入。

        读相机的壳层位移在树里现读活动相机,输出像素数变了只重灌那两格(push_screen_size),骨改名了只重接基座
        (apply_rig_basis),两条都不进这里。camera 是宿主契约里的参数,树里读的是场景的活动相机。"""
        scene = bpy.context.scene
        screen = self._screen_size(scene)
        done = 0
        linked = []
        for obj in objects:
            if obj.type != 'MESH' or obj.data is None:
                continue
            if obj.library is not None:
                # 库的数据不许指派本会话的树(Blender 静默不认),它画原始网格:要顶点腿的对象在本文件得是本地对象。
                if obj.modifiers.get(self.VTX_MODIFIER) is not None:
                    linked.append(obj.name_full)
                continue
            slots = self._stack_slots(obj)
            # 家族声明本栈的对象不进宿主阴影图(卡通着色经 ShadowAttenuation 自己求遮蔽)才关;
            # 其余家族投不投影由导入时的游戏数据定(shader 有没有启用的 ShadowCaster 趟),这里不碰。
            if slots and not self.HOST_SHADOW_CASTERS and obj.visible_shadow:
                obj.visible_shadow = False
            # 只进阴影图的渲染器(导入时按游戏的 ShadowsOnly 关掉了全部非阴影可见性)一个像素都画不出:
            # 真源只给它跑投影趟,壳与描边建出来也没人看得见,只是白付每一次几何重算。
            if not self._drawn_into_views(obj):
                slots = []
            vert_slots = [(i, m) for i, m in slots if m['ruri_uber_part'] in self.VERTEX_PARTS]
            for capability, prepare in CAP_PREPARERS.items():
                asking = [slot for slot, mat in slots if self._asks(mat['ruri_uber_part'], capability)]
                if asking:
                    prepare(obj, asking, [slot for slot, _mat in slots if slot not in asking])

            def _outline_on(mat):
                # 真判据四连:①材质**自己的 shader**(导入陈述里的 pass 表)有一趟,标签在该 part 声明的描边标签之列;
                # ②材质没按这个标签把它关掉;③宽度>0;④_BaseColor.a>0。相机在否不参与判据。
                # 标签按 part 声明而不是按字样猜:同一 part 认领的几份 shader 把同一趟描边挂在不同标签下,
                # 标签也不一定带 outline 字样。①不能省:disabledShaderPasses 只记材质**主动关掉**的 pass,
                # shader 本就没有描边 pass 的部件(CharacterNPR_VFX)不在里面,而它的 _OutlineWidth
                # 是换 shader 时留下的历史键 —— 只看②③④就会给它凭空长一圈壳。
                declared = {str(t).lower() for t in (self.PART_META[mat['ruri_uber_part']].get('outline') or [])}
                present = {str(t).lower() for t in (mat.get('ruri_uber_shader_passes') or [])}
                disabled = {str(p).lower() for p in (mat.get('ruri_uber_disabled_passes') or [])}
                if not (declared & present) - disabled:
                    return False
                floats, _s, colors = self._mat_meta(mat)
                base_a = colors.get('_BaseColor', [1, 1, 1, 1])[3]
                return float(floats.get('_OutlineWidth', 0.0)) > 0.0 and float(base_a) > 0.0

            outline_slots = [(i, m) for i, m in slots if _outline_on(m)]
            # 顶点腿**只为几何而存在**。没有壳位移也没有描边 ⇒ 这个对象根本不该有修改器。
            if not vert_slots and not outline_slots:
                continue
            tree_name = self.VTX_TREE_PREFIX + obj.name
            old = bpy.data.node_groups.get(tree_name)
            mod = obj.modifiers.get(self.VTX_MODIFIER)
            knobs = self._vtx_knob_rows()
            state = {'keys': {}, 'templates': {}, 'values': {}}
            panels = {}
            if old is not None:
                bpy.data.node_groups.remove(old)
            mt = _host().plugin_data(bpy.data.node_groups.new(tree_name, 'GeometryNodeTree'))
            mt.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
            mt.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
            gin = mt.nodes.new('NodeGroupInput')
            gout = mt.nodes.new('NodeGroupOutput')

            def nd(t_):
                return mt.nodes.new(t_)

            def nattr(name, dtype):
                a = nd('GeometryNodeInputNamedAttribute')
                a.data_type = dtype
                a.inputs['Name'].default_value = name
                return a.outputs['Attribute']

            mat_idx = nattr('material_index', 'INT')

            def slot_sel(slot):
                eq = nd('ShaderNodeMath')
                eq.operation = 'COMPARE'
                mt.links.new(mat_idx, eq.inputs[0])
                eq.inputs[1].default_value = float(slot)
                eq.inputs[2].default_value = 0.5
                return eq.outputs[0]

            def swap_yz(sock):
                sep = nd('ShaderNodeSeparateXYZ')
                mt.links.new(sock, sep.inputs[0])
                comb = nd('ShaderNodeCombineXYZ')
                mt.links.new(sep.outputs[0], comb.inputs[0])
                mt.links.new(sep.outputs[2], comb.inputs[1])
                mt.links.new(sep.outputs[1], comb.inputs[2])
                return comb.outputs[0]

            tbn_cache = []

            def uv_tangent(swap):
                uv = nattr('UVMap', 'FLOAT_VECTOR')
                if swap:
                    sep = nd('ShaderNodeSeparateXYZ')
                    mt.links.new(uv, sep.inputs[0])
                    comb = nd('ShaderNodeCombineXYZ')
                    mt.links.new(sep.outputs[1], comb.inputs[0])
                    mt.links.new(sep.outputs[0], comb.inputs[1])
                    uv = comb.outputs[0]
                node = nd('GeometryNodeUVTangent')
                node.inputs['Method'].default_value = 'Exact'
                mt.links.new(uv, node.inputs['UV'])
                return node.outputs['Tangent']

            def tangent_basis():
                """(tangentOS, w) —— 宿主按 UV 现算的切线基,零烘焙属性。

                喂对调过的 UV 得到 v 方向的切线,符号由此反解。取的是 **Unity 口径**的 w:
                顶点段图把 tangentOS 先 Y/Z 对调换到 Unity 空间再用,那次换轴是反射
                (det = -1),叉积随之反号 ⇒ w_unity = -w_blender,所以写 cross(T,N) 而不是
                cross(N,T)。读属性那条老路要求每张网格
                事先烘好 ruri_tangent/ruri_tangent_sign,而 Named Attribute **缺属性与读到
                零向量无法区分**,改过拓扑的网格会保留属性名把新增 corner 填零 —— 切线基
                静默塌掉而一个字不报。"""
                if not tbn_cache:
                    tangent = uv_tangent(False)
                    tangent_v = uv_tangent(True)
                    normal = nd('GeometryNodeInputNormal').outputs['Normal']
                    cross = nd('ShaderNodeVectorMath')
                    cross.operation = 'CROSS_PRODUCT'
                    mt.links.new(tangent, cross.inputs[0])
                    mt.links.new(normal, cross.inputs[1])
                    dot = nd('ShaderNodeVectorMath')
                    dot.operation = 'DOT_PRODUCT'
                    mt.links.new(cross.outputs['Vector'], dot.inputs[0])
                    mt.links.new(tangent_v, dot.inputs[1])
                    sign = nd('ShaderNodeMath')
                    sign.operation = 'SIGN'
                    mt.links.new(dot.outputs['Value'], sign.inputs[0])
                    tbn_cache.append((tangent, sign.outputs['Value']))
                return tbn_cache[0]

            color_cache = []

            def vertex_color():
                """(rgb, a) —— 与片元腿同一个 'Color' 属性:同一个 uniform 两条腿不许各读各的。"""
                if not color_cache:
                    attribute = nattr('Color', 'FLOAT_COLOR')
                    separate = nd('FunctionNodeSeparateColor')
                    mt.links.new(attribute, separate.inputs['Color'])
                    color_cache.append((attribute, separate.outputs['Alpha']))
                return color_cache[0]

            wire_cache = {}

            def vertex_inputs():
                """顶点入口的几何输入(属性与片元腿同源):壳层组与描边组吃同一份。几何节点的字段在
                消费它的那个节点上求值,同一个输入节点接给几个组各自按自己那一段几何取值。"""
                if not wire_cache:
                    wire_cache.update({
                        'input_positionOS': nd('GeometryNodeInputPosition').outputs['Position'],
                        'input_normalOS': nd('GeometryNodeInputNormal').outputs['Normal'],
                        'input_tangentOS': tangent_basis()[0],
                        'input_tangentOS_w': tangent_basis()[1],
                        'input_texcoord': nattr('UVMap', 'FLOAT_VECTOR'),
                        'input_texcoord1': nattr('UV1', 'FLOAT_VECTOR'),
                        'input_texcoord2': nattr('UV2', 'FLOAT_VECTOR'),
                        'input_color': vertex_color()[0],
                        'input_color_w': vertex_color()[1]})
                return wire_cache

            def wire_vertex_inputs(node):
                for sock in node.inputs:
                    src = vertex_inputs().get(sock.name)
                    if src is not None:
                        mt.links.new(src, sock)

            geo = gin.outputs[0]
            # ① 壳层位移(共享模板 + 逐实例灌值,选区 = 材质槽;贴图与旋钮抬到修改器接口)。
            for slot, mat in vert_slots:
                part = mat['ruri_uber_part']
                template_name = self.VERTEX_PARTS[part]
                gn = nd('GeometryNodeGroup')
                gn.node_tree = self.group(template_name)
                gn['ruri_vtx_mat'] = mat.name
                floats, st, colors = self._mat_meta(mat)
                self._fill_uniform_sockets(gn, floats, st, colors)
                wire_vertex_inputs(gn)
                self._write_screen_sockets(gn, screen)
                self._vtx_expose(mt, gin, gn, mat, knobs, panels, template_name, state,
                                 self._material_images(mat))
                self._vtx_own_samplers(gn, self._material_images(mat))
                out_sock = gn.outputs.get('position')
                if out_sock is None:
                    continue
                sp = nd('GeometryNodeSetPosition')
                mt.links.new(geo, sp.inputs['Geometry'])
                mt.links.new(slot_sel(slot), sp.inputs['Selection'])
                mt.links.new(swap_yz(out_sock), sp.inputs['Position'])
                geo = sp.outputs['Geometry']
            # ② 反壳描边 = 同树虚拟几何:本体原样复制一份、删无描边面、写 ruri_outline 面属性,作为**实例**并进本体的几何集。
            #    不实现化:两张不同网格 Join 要把整份顶点组逐组搬一遍(实测 929 组的身体每帧 94 ms,实例 0.9 ms),
            #    而壳在修改器栈末尾,之后没人读顶点组。实例几何的面属性与物体属性材质照样读得到。
            #    这里只有壳的拓扑:它往外挪多少是按正在画的那个视图算的,落在材质的位移出口上(描边视图段),
            #    宿主在顶点着色阶段逐视图求值 —— 这棵树不读相机,转视角一次都不重算。
            if outline_slots:
                keep = None
                for slot, _mat in outline_slots:
                    sel = slot_sel(slot)
                    if keep is None:
                        keep = sel
                    else:
                        mx = nd('ShaderNodeMath')
                        mx.operation = 'MAXIMUM'
                        mt.links.new(keep, mx.inputs[0])
                        mt.links.new(sel, mx.inputs[1])
                        keep = mx.outputs[0]
                drop = nd('ShaderNodeMath')
                drop.operation = 'SUBTRACT'
                drop.inputs[0].default_value = 1.0
                mt.links.new(keep, drop.inputs[1])
                dg = nd('GeometryNodeDeleteGeometry')
                dg.domain = 'FACE'
                mt.links.new(gin.outputs[0], dg.inputs['Geometry'])
                mt.links.new(drop.outputs[0], dg.inputs['Selection'])
                sa = nd('GeometryNodeStoreNamedAttribute')
                sa.data_type = 'FLOAT'
                sa.domain = 'FACE'
                sa.inputs['Name'].default_value = 'ruri_outline'
                sa.inputs['Value'].default_value = 1.0
                mt.links.new(dg.outputs['Geometry'], sa.inputs['Geometry'])
                shell = nd('GeometryNodeGeometryToInstance')
                mt.links.new(sa.outputs['Geometry'], shell.inputs[0])
                jn = nd('GeometryNodeJoinGeometry')
                mt.links.new(geo, jn.inputs[0])
                mt.links.new(shell.outputs[0], jn.inputs[0])
                geo = jn.outputs['Geometry']
            mt.links.new(geo, gout.inputs[0])
            _frame_generated(mt)
            mt['ruri_vtx_keys'] = state['keys']
            mt['ruri_vtx_templates'] = state['templates']
            if outline_slots:
                mt[self.VIEW_READER_KEY] = 1
            if mod is None:
                mod = obj.modifiers.new(self.VTX_MODIFIER, 'NODES')
            mod.node_group = mt
            # 自身网格的实例在编辑模式下会让 Blender 5.3 的 UV 编辑器解引用空的编辑笼崩溃(画实例的临时物体继承了
            # 本体的 data_orig 与编辑模式,求值结果却被清空);实现化要付 6 倍求值,所以带描边壳的树不进编辑模式求值。
            mod.show_in_editmode = not outline_slots
            # 换树时 Blender 按 identifier 顺延旧值,而 identifier 与「哪个材质的哪个属性」
            # 没有关系 —— 逐格显式写回,别让上一棵树的序号决定这一棵树的值。
            for identifier, value in state['values'].items():
                port = getattr(mod.properties.inputs, identifier, None)
                if port is None:
                    continue
                try:
                    port.value = value
                except (TypeError, ValueError):
                    pass
            # 脚本写修改器输入**不打依赖图标记**(实测 5.2.1:连 view_layer.update() 都刷不出来,
            # 只有 update_tag 才让求值副本跟上)。UI 拖滑块走的是另一条,不受影响。
            obj.update_tag()
            done += 1
            print('[ruri-vertex] {0}: shell x{1} outline x{2} knob x{3}'.format(
                obj.name, len(vert_slots), len(outline_slots), len(state['keys'])), flush=True)
        # 建出或拆掉的树可能多了或少了描边壳:读视图窗口的物体变了,宿主下一次写窗口前重新问一遍。
        if linked:
            print('[ruri-vertex] !! {0} 个 link 进来的对象挂着顶点腿,库的数据挂不上本会话的树,画的是原始网格 —— '
                  '把它们换成本地对象(数据照旧 link):{1}'.format(len(linked), linked[:8]), flush=True)
        _host().view_window_readers_changed()
        return done

    # ==================== 材质参数面板(读写路径;面板本体由宿主统一画) ====================

    def _panel_rows(self):
        for group in self.INTERFACE:
            for row in group['rows']:
                yield row

    def _panel_insts(self, mat):
        nt = mat.node_tree
        if nt is None:
            return []
        return sorted((n for n in nt.nodes if n.get('ruri_inst') is not None),
                      key=lambda n: int(n['ruri_inst']))

    def _panel_vertex_nodes(self, mat):
        """这张材质在顶点腿上的组实例。身份烙在实例上(建的时候写的),不靠「克隆组按材质命名」
        这种把身份编进名字的老办法 —— 模板现在是**共享**的,名字里没有材质。"""
        nodes = []
        for obj in bpy.data.objects:
            mod = obj.modifiers.get(self.VTX_MODIFIER)
            tree = getattr(mod, 'node_group', None) if mod is not None else None
            if tree is None:
                continue
            for node in tree.nodes:
                if node.get('ruri_vtx_mat') == mat.name:
                    nodes.append(node)
        return nodes

    def panel_claims(self, mat):
        # 判据 = 栈身份烙印(ruri_uber_part 每个栈都写,拿它当判据 = 一张材质被 N 个栈同时认领)。
        return mat is not None and mat.get('ruri_uber_stack') == self.PANEL_KEY

    def panel_rig(self, mat, armature=None):
        """基座骨这一行(None = 这张材质的 part 不用这座桥)。宿主交活骨架,生成物答
        「这张材质记的是哪根骨(Unity 身份)、在这副骨架上现在叫什么」。"""
        prop = self.RIG.get('prop') or ''
        if not prop or mat.get('ruri_uber_part') not in set(self.RIG.get('parts') or []):
            return None
        unity = self.rig_bone_of(mat)
        return {
            'prop': prop,
            'label': self.RIG.get('label') or prop,
            'unity': unity,
            'bone': self.rig_resolve_bone(armature, unity),
            'declared': self.RIG.get('bone') or '',
        }

    def panel_write_rig(self, mat, armature, bone_name):
        """选了一根活骨 → 记它的 **Unity 身份**,再给用这张材质的对象重接基座。

        存 Blender 骨名等于把这条绑定做成「改一次名就断且毫无痕迹」的东西,所以没有身份
        印记的骨直接拒收并说明白 —— 返回 (成功?, 说给用户听的话)。"""
        prop = self.RIG.get('prop') or ''
        if not prop:
            return False, '本栈没有骨骼基座桥。'
        if not bone_name:
            mat[prop] = ''
            return True, '已清空基座骨:这张材质回到绑定姿势基。'
        api = self._rig_identity_api('rig_unity_name_fn')
        unity = api(armature, bone_name) or '' if (api is not None and armature is not None) else ''
        if not unity:
            return False, "骨 '{0}' 没有导入器的身份印记,记下来改一次名就断 —— 换一根导入器建的骨。".format(bone_name)
        mat[prop] = unity
        users = [o for o in bpy.context.scene.objects
                 if o.type == 'MESH' and o.data is not None
                 and any(slot.material is mat for slot in o.material_slots)]
        self.apply_rig_basis(objects=users)
        return True, "基座骨 = {0}(Unity 身份 {1}),已给 {2} 个对象重接基座。".format(
            bone_name, unity, len(users))

    def panel_write(self, mat, row, value):
        """面板值 → 材质记录(真源)→ 组实例上的参数常量 + 顶点腿修改器口。面板里拧的是作者值(与 Unity 检视面
        同一个数),记录存作者值;推给顶点腿的那一份与 apply_params 同样线性化。"""
        name = row['name']
        kind = row['kind']
        srgb = name in self.SRGB_PARAMS
        if kind in ('SWITCH', 'VALUE', 'SLIDER', 'INT'):
            scalar = (1.0 if value else 0.0) if kind == 'SWITCH' else float(value)
            self._vtx_panel_write(mat, name, _srgb_to_linear(scalar) if srgb else scalar)
            floats = dict(mat.get('ruri_uber_floats') or {})
            floats[name] = scalar
            mat['ruri_uber_floats'] = floats
        else:
            vec4 = ([float(v) for v in value] + [0.0] * 4)[:4]
            xyz = ((_srgb_to_linear(vec4[0]), _srgb_to_linear(vec4[1]), _srgb_to_linear(vec4[2]))
                   if srgb else (vec4[0], vec4[1], vec4[2]))
            self._vtx_panel_write(mat, name, xyz)
            if row['size'] >= 4:
                self._vtx_panel_write(mat, name + '_w', vec4[3])
            colors = _mixed(mat.get('ruri_uber_colors'))
            colors[name] = vec4
            mat['ruri_uber_colors'] = colors
        self.apply_params(mat)

    def panel_write_image(self, mat, row, image):
        """贴图槽换图;清空 = 回落中性占位。没绑图的槽走导入期同一条 _wire_fetch/_sample 建线链。"""
        name = row['name']
        part = mat.get('ruri_uber_part', '')
        target = image
        imgs = dict(mat.get('ruri_uber_images') or {})
        if image is None:
            imgs.pop(name, None)
        else:
            imgs[name] = image
        mat['ruri_uber_images'] = imgs
        bound = dict(imgs)
        slot_rows = ([r[0] for r in self.part(part)['params'] if _image_row_slot(r[0]) == name]
                     if part in self.m['parts'] else [])
        if slot_rows:
            cols = _mixed(mat.get('ruri_uber_colors'))
            fresh = self._image_rows(part, {name: target} if target is not None else {})
            for row_name in slot_rows:
                if row_name in fresh:
                    cols[row_name] = fresh[row_name]
                else:
                    cols.pop(row_name, None)
            mat['ruri_uber_colors'] = cols

        def swap(tree, depth=0):
            if tree is None or depth > 4 or tree.library is not None:
                return
            for node in tree.nodes:
                if node.type == 'TEX_IMAGE' and _slot_of(node.label or '') == name:
                    if target is not None:
                        _swap_image(node, target, bound)
                elif node.type == 'TEX_IMAGE' and node.get(OWN_SAMPLER_KEY) == name:
                    # 别的槽借这一槽的采样器:借出的图换了,借用方的寻址与过滤跟着换。
                    _apply_own_sampler(node, target)
                elif node.type == 'GROUP':
                    swap(node.node_tree, depth + 1)

        swapped = [0]
        if mat.node_tree is not None:
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and _slot_of(node.label or '') == name:
                    if target is not None:
                        _swap_image(node, target, bound)
                        swapped[0] += 1
                elif node.type == 'TEX_IMAGE' and node.get(OWN_SAMPLER_KEY) == name:
                    _apply_own_sampler(node, target)
                elif node.type == 'GROUP':
                    swap(node.node_tree, 1)
        if not swapped[0] and target is not None and image is not None and mat.node_tree is not None \
                and part in self.m['parts']:
            g = G(mat.node_tree, is_group=False)
            ordered = self._panel_insts(mat)
            spec = self.part(part)
            for fetch in spec['fetches']:
                if fetch['slot'] == name and not fetch['env']:
                    self._wire_fetch(g, part, ordered, fetch, target, bound)
            for zone in spec['zones']:
                zone_rows = [f for f in zone['fetches'] if f['slot'] == name and not f['env']]
                if not zone_rows:
                    continue
                binsts = sorted((n for n in mat.node_tree.nodes if n.get('ruri_zone') == zone['sock']),
                                key=lambda n: int(n['ruri_binst']))
                if not binsts:
                    continue
                for fetch in zone_rows:
                    src = binsts[fetch['depth']]
                    heads = binsts[fetch['depth'] + 1:]
                    color, alpha, _anchor = self._sample(g, part, fetch, target,
                                                         src.outputs[fetch['sock'] + '_uv'], bound)
                    self._feed(g, heads, fetch['sock'], color, alpha)
            _frame_generated(mat.node_tree)
        # 顶点腿的槽:模板是共享的,**不许**往它的图节点上写(那是所有材质共用的一份)。
        # 这里只借模板接口那格的中性占位图判色彩空间,值本身经修改器端口落到本材质那一格。
        for group_node in self._panel_vertex_nodes(mat):
            self._vtx_own_samplers(group_node, {name: target})
            sub = group_node.node_tree
            if sub is None or target is None:
                continue
            for item in sub.interface.items_tree:
                if item.item_type == 'PANEL' or item.in_out != 'INPUT' or item.name != name:
                    continue
                holder = getattr(item, 'default_value', None)
                non_color = (holder is not None and hasattr(holder, 'colorspace_settings')
                             and holder.colorspace_settings.name == 'Non-Color')
                _set_colorspace(target, 'Non-Color' if non_color else 'sRGB')
                if non_color:
                    _fix_two_channel_layout(target)
        self._vtx_panel_write(mat, name, target)
        self.apply_params(mat)

    def panel_write_st(self, mat, row, tiling, offset):
        """平铺/偏移 → 材质记录 → 着色组的 _ST 参数常量 + 顶点期 uv 变换节点 + 顶点树修改器口(同一真值三消费面)。"""
        name = row['name']
        st_value = [float(tiling[0]), float(tiling[1]), float(offset[0]), float(offset[1])]
        st = _mixed(mat.get('ruri_uber_st'))
        st[name] = st_value
        mat['ruri_uber_st'] = st
        self._vtx_panel_write(mat, name + '_ST', (st_value[0], st_value[1], st_value[2]))
        self._vtx_panel_write(mat, name + '_ST_w', st_value[3])
        if row.get('st_node') and mat.node_tree is not None:
            for node in mat.node_tree.nodes:
                if node.label == row['st_node']:
                    node.inputs['Scale'].default_value = (st_value[0], st_value[1], 1.0)
                    node.inputs['Location'].default_value = (st_value[2], st_value[3], 0.0)
        self.apply_params(mat)

    @staticmethod
    def _panel_bound_images(mat):
        """{槽: 图}:材质自己的图节点只建在材质树顶层(_teximage;循环体也在同一棵树里),模板组里只有占位图 ——
        不下钻组。整棵树只走一遍,同槽取第一枚绑了真图的。"""
        found = {}
        if mat.node_tree is None:
            return found
        for node in mat.node_tree.nodes:
            if node.type != 'TEX_IMAGE':
                continue
            img = node.image
            if img is not None and not img.get('ruri_placeholder'):
                found.setdefault(_slot_of(node.label or ''), img)
        return found

    def _param_declared(self, part):
        """part 各参数行的声明缺省(作者值,四分量);面板上记录没说的那几格显示它。"""
        if part not in self.m['parts']:
            return {}
        return {name: [float(declared[0]), float(declared[1]), float(declared[2]), float(declared_w)]
                for name, _kind, declared, declared_w in self.part(part)['params']}

    def panel_read(self, mat):
        """面板上每一格的当前值:记录说了取记录,没说取声明缺省 —— 都是作者值。"""
        values, images, st_out = {}, {}, {}
        if not self._panel_insts(mat):
            return {'values': values, 'images': images, 'st': st_out}
        declared = self._param_declared(mat.get('ruri_uber_part', ''))
        floats = dict(mat.get('ruri_uber_floats') or {})
        st = _mixed(mat.get('ruri_uber_st'))
        colors = _mixed(mat.get('ruri_uber_colors'))
        bound = dict(mat.get('ruri_uber_images') or {})
        in_tree = None
        for row in self._panel_rows():
            name = row['name']
            kind = row['kind']
            if kind == 'TEXTURE':
                img = bound.get(name)
                if img is None:
                    if in_tree is None:
                        in_tree = self._panel_bound_images(mat)
                    img = in_tree.get(name)
                images[name] = img
                value = st.get(name) or declared.get(name + '_ST')
                if value is not None:
                    st_out[name] = (value[0], value[1], value[2], value[3])
                continue
            if kind in ('SWITCH', 'VALUE', 'SLIDER', 'INT'):
                value = floats.get(name)
                if value is None and name in declared:
                    value = declared[name][0]
                if value is None:
                    continue
                values[name] = (bool(value > 0.5) if kind == 'SWITCH'
                                else int(value) if kind == 'INT' else float(value))
            else:
                value = colors.get(name) or declared.get(name)
                if value is None:
                    continue
                spread = (list(value) + [0.0] * 4)[:row['size']]
                values[name] = tuple(float(x) for x in spread)
        return {'values': values, 'images': images, 'st': st_out}

    def _panel_slots(self, mat):
        part = mat.get('ruri_uber_part', '')
        slots = set(dict(mat.get('ruri_uber_images') or {}).keys())
        if part in self.m['parts']:
            for fetch in self._part_fetches(self.part(part)):
                slots.add(fetch['slot'])
        for group_node in self._panel_vertex_nodes(mat):
            sub = group_node.node_tree
            if sub is None:
                continue
            for node in sub.nodes:
                if node.bl_idname == 'GeometryNodeImageTexture' and node.label:
                    slots.add(_slot_of(node.label))
        return slots

    def panel_rows(self, mat):
        """这张材质真正有的行(判据 = 图自己:变体折叠的死支参数没有 socket)。"""
        insts = self._panel_insts(mat)
        slots = self._panel_slots(mat)
        out = []
        for group in self.INTERFACE:
            rows = []
            for row in group['rows']:
                if row['kind'] == 'TEXTURE':
                    if row['name'] not in slots:
                        continue
                    has_st = bool(row.get('st_node')) or any(
                        grp.inputs.get(row['name'] + '_ST') is not None for grp in insts)
                    row = dict(row, has_st=has_st)
                elif not any(grp.inputs.get(row['name']) is not None for grp in insts):
                    continue
                rows.append(row)
            if rows:
                out.append({'name': group['name'], 'gate': group['gate'], 'rows': rows})
        return out
    # ==================== 合成器后处理级(kind = post 的栈) ====================
    # Blender 5.2 契约(真渲染实测,写错是静默全黑):渲染结果只能由组内 CompositorNodeRLayers 取,
    # 出口走 NodeGroupOutput;喂组输入 socket 拿到的是全 0。

    POST_SAVED_KEY = 'ruri_post_saved_state'

    def _post_remember(self, scene):
        if self.POST_SAVED_KEY in scene:
            return
        previous = scene.compositing_node_group
        scene[self.POST_SAVED_KEY] = {
            'group': previous.name if previous is not None else '',
            'use_compositing': scene.render.use_compositing,
            'view_transform': scene.view_settings.view_transform,
            'look': scene.view_settings.look,
        }

    def installed(self, scene):
        """本会话装在这个场景上的本级:场景的合成树就是本级建的那棵(插件数据,存盘不带,开文件时由派生阶段现装)。"""
        tree = bpy.data.node_groups.get(self.post['scene_tree'])
        return tree is not None and tree.is_runtime_data and scene.compositing_node_group is tree

    def stage_node(self, scene):
        tree = scene.compositing_node_group
        return self._stage_in(tree) if tree is not None else None

    def _stage_in(self, tree):
        """树里本级的主入口:建树时第四枚就是它,按树序找到即停。"""
        for node in tree.nodes:
            if node.bl_idname == 'CompositorNodeGroup' and node.node_tree is not None \
                    and node.node_tree.name == self.post['group']:
                return node
        return None

    def extra_inputs(self):
        """颜色之外、由宿主驱动的输入口名(内核形参顺序)。"""
        return list(self.post.get('extra_in') or [])

    def extra_images(self):
        """内核贴图形参对应的组图像口名(内核形参顺序)。"""
        return list(self.post.get('images') or [])

    def grades(self, scene):
        """这一级调的画面在不在这个场景里:场景里有本配方材质栈建的材质。一个会话装着几个游戏的栈时,
        合成树只有一棵,凭这一条挑,不凭谁最后装。"""
        stacks = set(self.post.get('grades') or ())
        for obj in scene.objects:
            for slot in getattr(obj, 'material_slots', ()):
                material = slot.material
                if material is not None and material.get('ruri_uber_stack') in stacks:
                    return True
        return False

    POST_IMAGE_KEY = 'ruri_post_image'

    def set_images(self, scene, images):
        """按 extra_images() 往本级组的图像口接图。images[口名] = (宽, 高, 逐纹素 RGBA 浮点平铺)。纹素是关卡的
        数据,记在关卡的世界上随文件走;图是插件数据,每次建树时按记下的纹素现建(_wire_images)。模板组不烙任何图。
        缺一口就拒绝,理由同 set_extra。"""
        names = self.extra_images()
        missing = [name for name in names if name not in images]
        if missing:
            raise KeyError('[ruri-post] 本级要图像口 {0},缺 {1}'.format(names, missing))
        if not self.installed(scene):
            raise RuntimeError('[ruri-post] 场景上没装本级 {0}:先装本级再接图'.format(self.post['group']))
        stored = {}
        for name in names:
            width, height, texels = images[name]
            if len(texels) != width * height * 4:
                raise ValueError('[ruri-post] 图像口 {0} 给了 {1} 个分量,{2}x{3} RGBA 要 {4} 个'.format(
                    name, len(texels), width, height, width * height * 4))
            stored[name] = {'size': [int(width), int(height)], 'texels': [float(texel) for texel in texels]}
        self._store(scene, self.POST_IMAGES_KEY, stored)
        self._wire_images(scene)
        return len(names)

    def _wire_images(self, scene):
        """按场景记下的纹素现建本级的图像口图(逐场景一张,Non-Color 浮点,插件数据)接到主入口上;没记过的口留着
        模板组自己的缺省。"""
        node = self.stage_node(scene)
        tree = scene.compositing_node_group
        stored = self._stored(scene, self.POST_IMAGES_KEY)
        for name in self.extra_images():
            entry = stored.get(name)
            if entry is None:
                continue
            width, height = (int(value) for value in entry['size'])
            image_name = '{0} {1} {2}'.format(self.post['group'], name, scene.name)
            image = bpy.data.images.get(image_name)
            if image is not None:
                bpy.data.images.remove(image)
            image = _host().plugin_data(bpy.data.images.new(image_name, width, height, alpha=True, float_buffer=True,
                                                            is_data=True))
            image.colorspace_settings.name = 'Non-Color'
            image.pixels.foreach_set([float(texel) for texel in entry['texels']])
            # 改了像素不 update,合成器读到的还是旧缓冲(实测全 0)。打成浮点包只为撤销与关文件不喊「图像未保存」。
            image.update()
            image.file_format = 'OPEN_EXR'
            _image_stored(image)
            source = next((candidate for candidate in tree.nodes
                           if candidate.bl_idname == 'CompositorNodeImage'
                           and candidate.get(self.POST_IMAGE_KEY) == name), None)
            if source is None:
                source = tree.nodes.new('CompositorNodeImage')
                source[self.POST_IMAGE_KEY] = name
                source.label = name
            source.image = image
            tree.links.new(source.outputs['Image'], node.inputs[name])
        _frame_generated(tree)

    POST_CHAIN_KEY = 'ruri_post_chain'
    # 渲染结果拆三通道再拼成向量的那一枚:没有替换场景色的链时主入口的颜色口接它。
    POST_PACK_KEY = 'ruri_post_scene_color'
    # 记下的本级输入(内容,随文件走;合成树是插件数据,每次按它们现建),各按本级的组名分格:
    # 关卡写的宿主输入 {输入名: 分量}、关卡给的图像口纹素 —— 关卡的数据,住在关卡的世界上(与关卡全局量同处,
    # 用着关卡世界的场景看到的是同一份);面板上拧得与关卡不一样的主入口口 {口名: 分量} 是这个场景自己的。
    POST_VALUES_KEY = 'ruri_post_values'
    POST_KNOBS_KEY = 'ruri_post_knobs'
    POST_IMAGES_KEY = 'ruri_post_images'
    POST_SIZE_KEY = 'ruri_post_size'
    LEVEL_KEYS = (POST_VALUES_KEY, POST_IMAGES_KEY)

    def _holder(self, scene, key):
        if key not in self.LEVEL_KEYS:
            return scene
        if key in scene:
            raise RuntimeError('[ruri-post] 场景 {0} 把关卡陈述的调色记在场景上(旧格式):它住在关卡的世界上 —— '
                               '重新导入关卡'.format(scene.name))
        return scene.world

    def _stored(self, scene, key):
        """记下的本级那一格(POST_VALUES_KEY / POST_KNOBS_KEY / POST_IMAGES_KEY),纯 python。"""
        holder = self._holder(scene, key)
        if holder is None:
            return {}
        return _plain((holder.get(key) or {}).get(self.post['group'])) or {}

    def _store(self, scene, key, entries):
        holder = self._holder(scene, key)
        if holder is None:
            raise RuntimeError('[ruri-post] 场景 {0} 没有世界:关卡陈述的调色住在关卡的世界上'.format(scene.name))
        table = _plain(holder.get(key)) or {}
        table[self.post['group']] = entries
        holder[key] = table

    @staticmethod
    def _components(value):
        return [float(component) for component in value] if hasattr(value, '__len__') else [float(value)]

    def _expected_socket_values(self, scene, stage):
        """主入口各口按「接口缺省 → 关卡写的宿主输入」该有的值 {口名: 分量} —— 面板没拧过时口上就是它。
        宿主输入按 _write_input 的载体约定落口:四维 = 向量口 + _w 口,二维 = z 恒 0 的向量口。"""
        expected = {}
        for item in stage.node_tree.interface.items_tree:
            if getattr(item, 'in_out', '') == 'INPUT' and hasattr(item, 'default_value'):
                expected[item.name] = self._components(item.default_value)
        sizes = self.post['sizes']
        for name, components in self._stored(scene, self.POST_VALUES_KEY).items():
            socket = stage.inputs.get(name)
            if socket is None:
                continue
            if socket.type == 'VECTOR':
                expected[name] = (list(components[:3]) + [0.0] * 3)[:3]
                if sizes.get(name) == 4 and stage.inputs.get(name + '_w') is not None:
                    expected[name + '_w'] = [components[3]]
            else:
                expected[name] = [components[0]]
        return expected

    def _capture_knobs(self, scene):
        """面板直接拧的是运行时树上主入口的口:把拧得与「接口缺省 → 关卡输入」不一样的那些记回场景
        (POST_KNOBS_KEY),建树时写回。值没变不写 —— 写场景会让它变脏。"""
        stage = self.stage_node(scene)
        if stage is None:
            return False
        expected = self._expected_socket_values(scene, stage)
        knobs = {}
        for socket in stage.inputs:
            if socket.is_linked or not hasattr(socket, 'default_value'):
                continue
            try:
                value = self._components(socket.default_value)
            except (TypeError, ValueError):
                continue
            want = expected.get(socket.name)
            if want is None or len(want) != len(value) or any(
                    abs(have - should) > 1e-6 * max(1.0, abs(should)) for have, should in zip(value, want)):
                knobs[socket.name] = value
        if knobs == self._stored(scene, self.POST_KNOBS_KEY):
            return False
        self._store(scene, self.POST_KNOBS_KEY, knobs)
        return True
    POST_LEVEL_KEY = 'ruri_post_level'

    def _stage_nodes(self, tree):
        """本级在场景树里的全部组实例:主入口与图链里的各趟。"""
        names = {self.post['group']} | {entry['group'] for entry in (self.post.get('passes') or {}).values()}
        return [node for node in tree.nodes
                if node.bl_idname == 'CompositorNodeGroup' and node.node_tree is not None and node.node_tree.name in names]

    @staticmethod
    def _write_input(node, name, value, size):
        """往一个组实例写一个数值口。四维形参由向量口加 _w 口承载、二维由 z 恒 0 的向量口承载(生成器的载体约定);
        分量数照清单声明校验,对不上就拒绝。组上没有这个口(或四维口没有 _w)= 生成器把没人读的那部分剪掉了。"""
        components = tuple(float(component) for component in value) if hasattr(value, '__len__') else (float(value),)
        if len(components) != size:
            raise ValueError('[ruri-post] {0} 声明 {1} 个分量,给了 {2} 个'.format(name, size, len(components)))
        socket = node.inputs.get(name)
        if socket is None:
            return False
        if socket.type == 'VECTOR':
            fourth = node.inputs.get(name + '_w')
            if size == 4 and fourth is not None:
                fourth.default_value = components[3]
            socket.default_value = (components[:3] + (0.0,) * 3)[:3]
        else:
            socket.default_value = components[0]
        return True

    def _chain_inputs(self):
        """只由图链读、不进任何内核的宿主输入(各链的分辨率档)。"""
        return {chain['resolution_input'] for chain in (self.post.get('chains') or [])}

    def _unbuilt_inputs(self, tree):
        """此刻没建在树上的图链:只长在它各趟上的输入口无处可写(写下的值记在树上,链建出来时照原样写回)。
        判据是树上有没有记着这条链种类的趟(几条链可以共用一个趟组,按组认链会把没建的链当成建了),不是链该不该开
        —— 值先写、链后判的那一拍里,关卡刚打开的链还没建。"""
        passes = {entry['group']: entry for entry in (self.post.get('passes') or {}).values()}
        built = {node.get(self.POST_CHAIN_KEY) for node in self._stage_nodes(tree)}
        names = set()
        for chain in self.post.get('chains') or []:
            if chain['kind'] not in built:
                for group in chain['roles'].values():
                    names.update(passes[group]['inputs'])
        return names

    def _write_values(self, tree, values):
        """把 {名: 分量} 写进本级各组实例上的同名口(主入口与图链各趟一并写)。"""
        nodes = self._stage_nodes(tree)
        unplaced = self._chain_inputs() | self._unbuilt_inputs(tree)
        sizes = self.post['sizes']
        for name, value in values.items():
            if not any([self._write_input(node, name, value, sizes[name]) for node in nodes]) and name not in unplaced:
                raise KeyError('[ruri-post] 本级各组上都没有输入口 {0}'.format(name))

    def set_extra(self, scene, values):
        """按 extra_inputs() 的顺序写宿主驱动的输入口:同名口在主入口与图链各趟上是同一个值,一并写;写下的值记在
        关卡的世界上(内容,随文件走),建树与图链按新尺寸或新分辨率档重建时照原样写回。缺一口就拒绝 —— 半份参数比不接更坏
        (0 是恒等,漏掉的那一口会悄悄回到恒等,画面差一点点而没人知道)。"""
        names = self.extra_inputs()
        if len(values) != len(names):
            raise ValueError('[ruri-post] 本级要 {0} 个宿主输入 {1},收到 {2} 个'.format(
                len(names), names, len(values)))
        if not self.installed(scene):
            raise RuntimeError('[ruri-post] 场景上没装本级 {0}:先装本级再写输入,写进空处 = 这份调色静默丢失'.format(
                self.post['group']))
        tree = scene.compositing_node_group
        remembered = {name: [float(component) for component in value] if hasattr(value, '__len__') else [float(value)]
                      for name, value in zip(names, values)}
        self._store(scene, self.POST_VALUES_KEY, remembered)
        # 先写后判:开关口长在主入口上的链按主入口此刻的值开关,判之前值得已经在口上;重建时记下的值照原样写回各趟。
        self._write_values(tree, remembered)
        self.refresh_chains(scene)
        # 关卡刚写的这几格从此就是主入口「该有」的值,面板旋钮的记账跟着换基准。
        self._capture_knobs(scene)
        return len(names)

    @staticmethod
    def _output_size(scene):
        render = scene.render
        return (max(1, render.resolution_x * render.resolution_percentage // 100),
                max(1, render.resolution_y * render.resolution_percentage // 100))

    def _chain_signature(self, scene, tree):
        """图链的拓扑由出图尺寸、各链的分辨率档、开关与视口预览定;没写过分辨率档时按半分辨率(真源 BloomResolution.Half = 2)。"""
        remembered = self._stored(scene, self.POST_VALUES_KEY)
        width, height = self._output_size(scene)
        signature = [width, height] + [float(remembered[name][0]) if name in remembered else 2.0
                                       for name in sorted(self._chain_inputs())]
        chains = self.post.get('chains') or []
        return signature + [1.0 if self._chain_enabled(scene, tree, chain) else 0.0
                            for chain in chains if chain.get('enable_input')] + [
            1.0 if self._viewport_preview(scene, chain) else 0.0 for chain in chains
            if chain['kind'] in self.RENDER_ONLY_KINDS] + [
            float(len(self._water_objects(scene, chain))) for chain in chains if chain['kind'] == 'water_lighting']

    POST_VIEWPORT_KEY = 'ruri_post_viewport'
    # 替换场景色的链:视口里不算它 = 视口直接看没补过的渲染结果,是精确的「这条链不在」。
    # 泛光是主入口按强度插过去的一张图,喂什么都不是恒等,只能整条开关(强度 0 就不建,见 _chain_enabled)。
    RENDER_ONLY_KINDS = ('screen_space_reflection', 'water_lighting')
    # 能在视口里预览的那几种:水面要第二个视图层,视口的合成器只看得到活动视图层。
    PREVIEWABLE_KINDS = ('screen_space_reflection',)

    def viewport_preview_kinds(self):
        """能在视口里按需开关的链(面板照它画开关)。"""
        return [chain['kind'] for chain in (self.post.get('chains') or []) if chain['kind'] in self.PREVIEWABLE_KINDS]

    def viewport_previewed(self, scene, kind):
        """这条链在视口里也算吗:缺省只在最终渲染里算(视口的合成器每画一帧就把整条链从头跑一遍,转视角、
        采样收敛的每一拍都在付);面板上点了「视口预览」才在视口里算。记在场景上,随文件走。"""
        return kind in self.PREVIEWABLE_KINDS and bool(self._stored(scene, self.POST_VIEWPORT_KEY).get(kind))

    def _viewport_preview(self, scene, chain):
        return self.viewport_previewed(scene, chain['kind'])

    def set_viewport_preview(self, scene, kind, enabled):
        if kind not in self.PREVIEWABLE_KINDS:
            raise ValueError('[ruri-post] 图链 {0} 不能单在视口里开关(只有 {1} 能)'.format(kind, self.PREVIEWABLE_KINDS))
        stored = self._stored(scene, self.POST_VIEWPORT_KEY)
        if enabled:
            stored[kind] = 1
        else:
            stored.pop(kind, None)
        self._store(scene, self.POST_VIEWPORT_KEY, stored)
        return self.refresh_chains(scene)

    def _render_only(self, tree, label, rendered, viewed):
        """只在最终渲染里走 rendered,视口走 viewed。合成器编排时按 Is Viewport 静态剪掉没走的那一支,
        视口里 rendered 那条链一个节点都不跑。"""
        switch = tree.nodes.new('GeometryNodeSwitch')
        switch.input_type = 'VECTOR'
        switch[self.POST_CHAIN_KEY] = label
        viewport = tree.nodes.new('GeometryNodeIsViewport')
        viewport[self.POST_CHAIN_KEY] = label
        tree.links.new(viewport.outputs[0], switch.inputs['Switch'])
        tree.links.new(rendered, switch.inputs['False'])
        tree.links.new(viewed, switch.inputs['True'])
        return switch.outputs[0]

    def _chain_enabled(self, scene, tree, chain):
        """链的开关:没有开关口的链恒开;有开关口的首分量大于 0 才开 —— 真源的 IsActive(泛光强度 > 0、反射开关为真)。
        开关口在主入口上时按主入口此刻的值(面板拧的就是这一口),只长在链自己各趟上时按关卡写下的值(没写过 = 关)。
        泛光强度为 0 时主入口的合成是恒等(按强度从原色插过去,因子 0),金字塔建出来也只是逐帧白算。"""
        name = chain.get('enable_input')
        if not name:
            return True
        # 主入口建在树头几枚,找到就停;别走 _stage_nodes —— 那是把上万枚图链节点整个扫一遍。
        stage = self._stage_in(tree)
        socket = stage.inputs.get(name) if stage is not None else None
        if socket is not None:
            value = socket.default_value
            return float(value[0] if hasattr(value, '__len__') else value) > 0.0
        remembered = self._stored(scene, self.POST_VALUES_KEY)
        return name in remembered and float(remembered[name][0]) > 0.0

    def _drop_level_domains(self, scene):
        """清掉本场景各图链的级域图(它们只被本级场景树里的图链节点用)。"""
        for image in [candidate for candidate in bpy.data.images if candidate.get(self.POST_LEVEL_KEY) == scene.name
                      and candidate.name.startswith(self.post['group'] + ' ')]:
            bpy.data.images.remove(image)

    def _level_domain(self, tree, scene, label, level, size):
        """一级金字塔的出图域:按该级尺寸建一张生成图,取它的坐标场。合成器的 Scale 只改变换、共享源像素、推迟到
        实化才重采样,拿它当域每一级都还在全分辨率网格上跑;生成图是恒等变换的真 w×h 网格,MapUV 只实化图像的
        变换、在图自己的归一化空间取样,跨级取样因此成立。"""
        image = _host().plugin_data(bpy.data.images.new(
            '{0} {1} level {2} {3}'.format(self.post['group'], label, level, scene.name),
            size[0], size[1], alpha=False, float_buffer=False, is_data=True))
        image[self.POST_LEVEL_KEY] = scene.name
        domain = tree.nodes.new('CompositorNodeImage')
        domain.image = image
        coordinates = tree.nodes.new('CompositorNodeImageCoordinates')
        tree.links.new(domain.outputs['Image'], coordinates.inputs['Image'])
        domain[self.POST_CHAIN_KEY] = label
        coordinates[self.POST_CHAIN_KEY] = label
        return coordinates.outputs['Normalized']

    CHAIN_KINDS = ('bloom_pyramid', 'screen_space_reflection', 'water_lighting')

    def _build_chains(self, scene, tree, render, stage):
        """场景树里现建各图链。替换场景色的链(屏幕空间反射)先建:它合成后的颜色才是主入口与泛光看到的场景色。
        建之前先记下这一版的签名:建链途中宿主会同步地发依赖图更新,sync_post_chains 由此重入 refresh_chains,
        见签名已是当前的就什么都不做 —— 不会中途拆掉外层攥着的节点;建到一半抛错也照样抛给调用方,但不会让之后
        每一拍依赖图更新都再建一遍(那是一个永不收敛的循环,导入整个挂死)。"""
        tree[self.POST_SIZE_KEY] = self._chain_signature(scene, tree)
        width, height = self._output_size(scene)
        remembered = self._stored(scene, self.POST_VALUES_KEY)
        self._drop_level_domains(scene)
        chains = self.post.get('chains') or []
        for chain in chains:
            if chain['kind'] not in self.CHAIN_KINDS:
                raise RuntimeError('[ruri-post] 图链种类 {0} 本运行时不会建'.format(chain['kind']))
        packed = next(node for node in tree.nodes if node.get(self.POST_PACK_KEY))
        scene_color = None
        for chain in chains:
            if chain['kind'] == 'screen_space_reflection' and self._chain_enabled(scene, tree, chain):
                preview = self._viewport_preview(scene, chain)
                scene_color = self._screen_space_reflection(tree, scene, render, chain, width, height, preview)
                if not preview:
                    scene_color = self._render_only(tree, chain['kind'], scene_color, packed.outputs['Vector'])
        for chain in chains:
            if chain['kind'] != 'water_lighting' or not self._chain_enabled(scene, tree, chain):
                continue
            beneath = scene_color if scene_color is not None else packed.outputs['Vector']
            composed = self._water_lighting(tree, scene, render, chain, width, height, beneath)
            if composed is not None:
                scene_color = self._render_only(tree, chain['kind'], composed, beneath)
        tree.links.new(scene_color if scene_color is not None else packed.outputs['Vector'],
                       stage.inputs[self.post['color_in']])
        for chain in chains:
            if chain['kind'] != 'bloom_pyramid' or not self._chain_enabled(scene, tree, chain):
                continue
            resolution = float(remembered[chain['resolution_input']][0]) if chain['resolution_input'] in remembered else 2.0
            source = scene_color if scene_color is not None else render.outputs['Image']
            tree.links.new(self._bloom_pyramid(tree, scene, source, chain, width, height, resolution),
                           stage.inputs[chain['image']])

    SSR_LEVELS = 7

    def _screen_space_reflection(self, tree, scene, render, chain, width, height, viewport):
        """屏幕空间反射(真源 V2 管线的调度,桌面档全分辨率):宿主深度 → 设备深度 → 六级最大值深度金字塔;追踪
        (分类 + 步进 + 锥)出反射色、锥 mip 与可信度;颜色与可信度按深度双边逐级降六级;解析取两级插值与可信度;
        最后按覆盖的规则把那一份补到渲染结果上,返回补过的颜色。各级尺寸 = 上一级整除 2(真源带 mip 的 RT)。
        相机量由场景树里现答的相机子树给;材质按覆盖询问存的 AOV 由渲染层节点按名取。真源 maxMipCount =
        min(log2(边) + 1, 7),出图不到 64 像素见方就不满七级 —— 那样的出图本栈不建这条链。"""
        if max(width, height) < 64:
            raise RuntimeError('[ruri-post] 屏幕空间反射的七级金字塔要出图至少 64 像素,实得 {0}x{1}'.format(width, height))
        label = chain['kind']
        passes = {entry['group']: entry for entry in (self.post.get('passes') or {}).values()}
        roles = {role: passes[group] for role, group in chain['roles'].items()}
        self._ssr_view_layers(scene, chain)
        render.update()
        sizes = [(width, height)]
        for _level in range(1, self.SSR_LEVELS):
            sizes.append((max(1, sizes[-1][0] // 2), max(1, sizes[-1][1] // 2)))
        full = tree.nodes.new('CompositorNodeImageCoordinates')
        full[self.POST_CHAIN_KEY] = label
        tree.links.new(render.outputs['Image'], full.inputs['Image'])
        fields = [full.outputs['Normalized']] + [self._level_domain(tree, scene, label, level, sizes[level])
                                                 for level in range(1, self.SSR_LEVELS)]
        camera = self._ssr_camera(tree, render, label, viewport)
        overlays = {}
        for image, aov in chain['overlays'].items():
            socket = render.outputs.get(aov)
            if socket is None:
                raise RuntimeError('[ruri-post] 渲染层没有 AOV {0}(视层登记之后渲染层节点仍不出这个口)'.format(aov))
            overlays[image] = socket

        def extent(size):
            return (float(size[0]), float(size[1]), 1.0 / size[0], 1.0 / size[1])

        def run(role, coordinate, images, values=None):
            entry = roles[role]
            node = tree.nodes.new('CompositorNodeGroup')
            node.node_tree = self.group(entry['group'])
            node[self.POST_CHAIN_KEY] = label
            tree.links.new(coordinate, node.inputs[entry['coords'][0]])
            if set(images) != set(entry['images']):
                raise RuntimeError('[ruri-post] {0} 要图 {1},链给了 {2}'.format(
                    entry['group'], sorted(entry['images']), sorted(images)))
            for name, image in images.items():
                tree.links.new(image, node.inputs[name])
            for name, value in (values or {}).items():
                if not self._write_input(node, name, value, entry['sizes'][name]):
                    raise KeyError('[ruri-post] {0} 上没有按级给的口 {1}'.format(entry['group'], name))
            for name, answer in camera.items():
                self._link_answer(tree, node, name, answer)
            if 'ssrFrameIndex' in entry['sizes'] and node.inputs.get('ssrFrameIndex') is not None:
                curve = node.inputs['ssrFrameIndex'].driver_add('default_value')
                curve.driver.expression = 'frame'
            return node

        def only(node, role):
            return node.outputs[roles[role]['out']]

        depths = [only(run('device_depth', fields[0], {'sceneDepth': render.outputs['Depth']}), 'device_depth')]
        for level in range(1, self.SSR_LEVELS):
            depths.append(only(run('depth_pyramid', fields[level], {'previousLevel': depths[-1]},
                                   {'previousSize': extent(sizes[level - 1]), 'levelSize': extent(sizes[level])}),
                               'depth_pyramid'))
        trace_images = {'deviceDepth': depths[0], 'sceneColor': render.outputs['Image']}
        trace_images.update({name: socket for name, socket in overlays.items() if name in roles['trace']['images']})
        trace = run('trace', fields[0], trace_images)
        colors = [trace.outputs['ret_color']]
        fades = [trace.outputs['ret_fade']]
        for level in range(1, self.SSR_LEVELS):
            reduced = run('color_pyramid', fields[level],
                          {'previousColor': colors[-1], 'previousFade': fades[-1], 'previousDepth': depths[level - 1],
                           'levelDepth': depths[level]},
                          {'previousSize': extent(sizes[level - 1]), 'levelSize': extent(sizes[level])})
            colors.append(reduced.outputs['ret_color'])
            fades.append(reduced.outputs['ret_fade'])
        resolve_images = {'coneMip': trace.outputs['ret_coneMip']}
        for level in range(self.SSR_LEVELS):
            resolve_images['depth{0}'.format(level)] = depths[level]
            resolve_images['color{0}'.format(level)] = colors[level]
            resolve_images['fade{0}'.format(level)] = fades[level]
        resolved = run('resolve', fields[0], resolve_images)
        composite_images = {'sceneColor': render.outputs['Image'], 'ssrLighting': resolved.outputs['ret_lighting'],
                            'ssrFade': resolved.outputs['ret_fade']}
        composite_images.update({name: socket for name, socket in overlays.items()
                                 if name in roles['composite']['images']})
        return only(run('composite', fields[0], composite_images), 'composite')

    WATER_LAYER = 'Ruri Water'

    def _water_objects(self, scene, chain):
        """水面物体:材质顶层树里写着这条链全部覆盖询问 AOV 的那些(询问落成同名 AOV,见 _wire_overlays)。"""
        wanted = set(chain['overlays'].values())
        materials = {material for material in bpy.data.materials if material.node_tree is not None and wanted <= {
            node.aov_name for node in material.node_tree.nodes if node.bl_idname == 'ShaderNodeOutputAOV'}}
        return [obj for obj in scene.objects if any(slot.material in materials for slot in obj.material_slots)]

    @staticmethod
    def _water_collections(scene, objects):
        """水面物体各自所在的集合:别的视图层要排除它们,所以它们只能装水面物体。"""
        members = set(objects)
        collections = {collection for obj in objects for collection in obj.users_collection}
        for collection in collections:
            if collection == scene.collection or not set(collection.all_objects) <= members:
                raise RuntimeError('[ruri-post] 水面物体要收在只装水面的集合里(场景层排除它,水面层画它):'
                                   '{0} 里还装着别的物体'.format(collection.name))
        return collections

    @staticmethod
    def _layer_entry(entry, collection):
        if entry.collection == collection:
            return entry
        for child in entry.children:
            found = Stack._layer_entry(child, collection)
            if found is not None:
                return found
        return None

    def _water_view_layer(self, scene, chain, collections, beneath):
        """水面层画全部内容 —— 水面像素上的深度、各通道与覆盖询问就是水面自己的,别的像素不读 —— 另开这条链读的
        通道与覆盖询问的 AOV;其余视图层一律排除水面的集合,它们的颜色与深度就是真源不含水的场景色与深度,
        链读的那一层(beneath)因此也要出深度。"""
        water = scene.view_layers.get(self.WATER_LAYER)
        if water is None:
            water = scene.view_layers.new(self.WATER_LAYER)
        scene.view_layers[beneath].use_pass_z = True
        for layer in scene.view_layers:
            for collection in collections:
                entry = self._layer_entry(layer.layer_collection, collection)
                if entry is None:
                    raise RuntimeError('[ruri-post] 视图层 {0} 里没有水面集合 {1}'.format(layer.name, collection.name))
                excluded = layer != water
                if entry.exclude != excluded:
                    entry.exclude = excluded
        water.use = True
        water.use_pass_z = True
        for spec in chain['layer_passes'].values():
            if not spec['enable']:
                continue
            owner = water
            path = spec['enable'].split('.')
            for part in path[:-1]:
                owner = getattr(owner, part)
            setattr(owner, path[-1], True)
        present = {aov.name for aov in water.aovs}
        for name in sorted(set(chain['overlays'].values())):
            if name not in present:
                aov = water.aovs.add()
                aov.name = name
                aov.type = 'COLOR'
        return water

    def _wire_level_inputs(self, tree, scene, node, label):
        """一趟组上读关卡的口:关卡全局(口名 = 全局名,四维的第四分量在 _w 口)按宿主写在关卡世界上的「值 - 缺省」用驱动灌
        —— 组里再加缺省,与着色树读视图层属性同一条编码,没写过的全局读 0 落在缺省本身;场景没有世界就是没有关卡,口停在 0。
        关卡图的口(取点落成的组图口,口名 = 槽名)接宿主按关卡建的那一张。"""
        world = scene.world
        for name in (self.level_global_bases() if world is not None else ()):
            for socket, components in ((node.inputs.get(name), (0, 1, 2)), (node.inputs.get(name + '_w'), (3,))):
                if socket is None:
                    continue
                for index, component in enumerate(components):
                    curve = socket.driver_add('default_value', index) if len(components) > 1 \
                        else socket.driver_add('default_value')
                    driver = curve.driver
                    driver.type = 'AVERAGE'
                    variable = driver.variables.new()
                    variable.type = 'SINGLE_PROP'
                    target = variable.targets[0]
                    target.id_type = 'WORLD'
                    target.id = world
                    target.data_path = '["{0}"][{1}]'.format(name, component)
        for slot in self.level_images:
            socket = node.inputs.get(slot)
            if socket is None or socket.is_linked:
                continue
            source = tree.nodes.new('CompositorNodeImage')
            source[self.POST_CHAIN_KEY] = label
            source.image = self._level_image(slot)
            tree.links.new(source.outputs['Image'], socket)

    def _water_lighting(self, tree, scene, render, chain, width, height, beneath):
        """水面(HG 的水面屏幕空间反射与 WaterLighting 读屏幕的后半段):没有水面物体时这条链不建,返回 None。场景层的
        深度与颜色是不含水的那份;水面层按同一台相机再画一遍,读它的深度、各通道与覆盖询问。两份深度各转设备深度,
        场景那份降成半分辨率低深度,反射在半分辨率上追踪,合成在出图分辨率上,返回合成后的场景色。"""
        objects = self._water_objects(scene, chain)
        if not objects:
            return None
        label = chain['kind']
        layer = self._water_view_layer(scene, chain, self._water_collections(scene, objects), render.layer)
        water = tree.nodes.new('CompositorNodeRLayers')
        water[self.POST_CHAIN_KEY] = label
        water.scene = scene
        water.layer = layer.name
        render.update()
        water.update()
        passes = {entry['group']: entry for entry in (self.post.get('passes') or {}).values()}
        roles = {role: passes[group] for role, group in chain['roles'].items()}
        half = (max(1, width // 2), max(1, height // 2))
        full = tree.nodes.new('CompositorNodeImageCoordinates')
        full[self.POST_CHAIN_KEY] = label
        tree.links.new(render.outputs['Image'], full.inputs['Image'])
        full_field = full.outputs['Normalized']
        half_field = self._level_domain(tree, scene, label, 1, half)
        camera = self._ssr_camera(tree, render, label, False)

        def output(owner, name, what):
            found = owner.outputs.get(name)
            if found is None:
                raise RuntimeError('[ruri-post] 水面层的渲染层节点没有{0} {1}(视图层登记之后仍不出这个口)'.format(what, name))
            return found

        layer_images = {image: output(water, aov, 'AOV') for image, aov in chain['overlays'].items()}
        layer_images.update({image: output(water, spec['output'], '通道')
                             for image, spec in chain['layer_passes'].items()})

        def extent(size):
            return (float(size[0]), float(size[1]), 1.0 / size[0], 1.0 / size[1])

        def run(role, coordinate, images, values=None):
            entry = roles[role]
            node = tree.nodes.new('CompositorNodeGroup')
            node.node_tree = self.group(entry['group'])
            node[self.POST_CHAIN_KEY] = label
            tree.links.new(coordinate, node.inputs[entry['coords'][0]])
            provided = dict(images)
            provided.update({name: layer_images[name] for name in entry['images'] if name in layer_images})
            if set(provided) != set(entry['images']):
                raise RuntimeError('[ruri-post] {0} 要图 {1},链给了 {2}'.format(
                    entry['group'], sorted(entry['images']), sorted(provided)))
            for name, image in provided.items():
                tree.links.new(image, node.inputs[name])
            for name, value in (values or {}).items():
                if not self._write_input(node, name, value, entry['sizes'][name]):
                    raise KeyError('[ruri-post] {0} 上没有按级给的口 {1}'.format(entry['group'], name))
            for name, answer in camera.items():
                self._link_answer(tree, node, name, answer)
            if 'ssrFrameIndex' in entry['sizes'] and node.inputs.get('ssrFrameIndex') is not None:
                curve = node.inputs['ssrFrameIndex'].driver_add('default_value')
                curve.driver.expression = 'frame'
            self._wire_level_inputs(tree, scene, node, label)
            return node

        def only(node, role):
            return node.outputs[roles[role]['out']]

        scene_depth = only(run('device_depth', full_field, {'sceneDepth': render.outputs['Depth']}), 'device_depth')
        water_depth = only(run('device_depth', full_field, {'sceneDepth': water.outputs['Depth']}), 'device_depth')
        low = only(run('low_depth', half_field, {'sceneDeviceDepth': scene_depth},
                       {'previousSize': extent((width, height))}), 'low_depth')
        reflect = run('reflect', half_field, {'waterDeviceDepth': water_depth, 'lowDepth': low, 'sceneColor': beneath},
                      {'levelSize': extent(half)})
        composite = run('composite', full_field, {
            'sceneColor': beneath, 'sceneDeviceDepth': scene_depth, 'waterDeviceDepth': water_depth,
            'reflectLighting': reflect.outputs['ret_lighting'], 'reflectFade': reflect.outputs['ret_fade']})
        return only(composite, 'composite')

    @staticmethod
    def _ssr_view_layers(scene, chain):
        """反射链读的渲染通道:深度与材质存的覆盖询问(一律按颜色 AOV 存,标量询问读它的第一个通道)。"""
        for layer in scene.view_layers:
            layer.use_pass_z = True
            present = {aov.name for aov in layer.aovs}
            for name in sorted(set(chain['overlays'].values())):
                if name not in present:
                    aov = layer.aovs.add()
                    aov.name = name
                    aov.type = 'COLOR'

    @staticmethod
    def _link_answer(tree, node, name, answer):
        """把相机子树的一个答案接到组的同名口:四维口 = 向量口 + _w 口;组上没有这个口 = 生成器把没人读的剪掉了。"""
        socket = node.inputs.get(name)
        if socket is None:
            return
        vector, fourth = answer
        tree.links.new(vector, socket)
        if fourth is not None and node.inputs.get(name + '_w') is not None:
            tree.links.new(fourth, node.inputs[name + '_w'])

    def _ssr_camera(self, tree, render, label, viewport):
        """反射链的相机量,在场景树里现答:渲染时是活动相机(Camera Info 的投影与物体变换),
        视口里是宿主的视点(物体变换 + 驱动读它身上记着的投影)—— 合成树只有 Is Viewport 分得清两者。
        视口那一支只在这条链开了视口预览时才建:没开时视口里整条链被剪掉,不建视点也不挂驱动 —— 视点每跟一步
        视图,驱动就把整棵合成树弄脏一次。
        投影按真源的 GPU 投影改写:反向 Z(第 3 行 ← −0.5·第 3 行 + 0.5·第 4 行),y 不翻(真源在乘前乘后自己翻);
        视旋转 = (相机到世界的旋转)ᵀ · 世界基(宿主世界 = 基 · 源世界);裁剪面按宿主投影反解(透视 near = B/(A−1)、
        far = B/(A+1),正交 near = (B+1)/A、far = (B−1)/A);出图尺寸取渲染结果自己的分辨率。
        返回 {组输入名: (向量或标量口, 第四分量口或 None)}。"""
        nodes = tree.nodes
        links = tree.links

        def nd(kind):
            node = nodes.new(kind)
            node[self.POST_CHAIN_KEY] = label
            return node

        def link_or_set(value, socket):
            if isinstance(value, (int, float)):
                socket.default_value = float(value)
            else:
                links.new(value, socket)

        def math(operation, a, b=None):
            node = nd('ShaderNodeMath')
            node.operation = operation
            link_or_set(a, node.inputs[0])
            if b is not None:
                link_or_set(b, node.inputs[1])
            return node.outputs[0]

        def combine(x, y, z):
            node = nd('ShaderNodeCombineXYZ')
            for axis, value in zip(('X', 'Y', 'Z'), (x, y, z)):
                link_or_set(value, node.inputs[axis])
            return node.outputs['Vector']

        def entry(node, column, row):
            return node.outputs['Column {0} Row {1}'.format(column + 1, row + 1)]

        def split(matrix):
            node = nd('FunctionNodeSeparateMatrix')
            links.new(matrix, node.inputs['Matrix'])
            return node

        def pick(kind, rendered, viewed):
            node = nd('GeometryNodeSwitch')
            node.input_type = kind
            links.new(nd('GeometryNodeIsViewport').outputs[0], node.inputs['Switch'])
            links.new(rendered, node.inputs['False'])
            links.new(viewed, node.inputs['True'])
            return node.outputs[0]

        active = nd('GeometryNodeInputActiveCamera').outputs[0]
        info = nd('GeometryNodeCameraInfo')
        links.new(active, info.inputs['Camera'])
        placed = nd('GeometryNodeObjectInfo')
        placed.transform_space = 'ORIGINAL'
        links.new(active, placed.inputs['Object'])
        transform = placed.outputs['Transform']
        projection_matrix = info.outputs['Projection Matrix']
        if viewport:
            view = self._host_module().viewpoint()
            viewed = nd('GeometryNodeObjectInfo')
            viewed.transform_space = 'ORIGINAL'
            viewed.inputs['Object'].default_value = view.object
            carried = nd('FunctionNodeCombineMatrix')
            for column in range(4):
                for row in range(4):
                    curve = carried.inputs['Column {0} Row {1}'.format(column + 1, row + 1)].driver_add('default_value')
                    driver = curve.driver
                    driver.type = 'AVERAGE'
                    variable = driver.variables.new()
                    variable.type = 'SINGLE_PROP'
                    target = variable.targets[0]
                    target.id_type = 'OBJECT'
                    target.id = view.object
                    target.data_path = '["{0}"][{1}]'.format(view.projection_property, row * 4 + column)
            transform = pick('MATRIX', transform, viewed.outputs['Transform'])
            projection_matrix = pick('MATRIX', projection_matrix, carried.outputs['Matrix'])
        host_projection = split(projection_matrix)

        gpu = nd('FunctionNodeCombineMatrix')
        for column in range(4):
            for row in range(4):
                value = entry(host_projection, column, row)
                if row == 2:
                    value = math('ADD', math('MULTIPLY', entry(host_projection, column, 2), -0.5),
                                 math('MULTIPLY', entry(host_projection, column, 3), 0.5))
                links.new(value, gpu.inputs['Column {0} Row {1}'.format(column + 1, row + 1)])
        inverse = nd('FunctionNodeInvertMatrix')
        links.new(gpu.outputs['Matrix'], inverse.inputs['Matrix'])
        projection = split(gpu.outputs['Matrix'])
        inverse_projection = split(inverse.outputs['Matrix'])

        parts = nd('FunctionNodeSeparateTransform')
        links.new(transform, parts.inputs['Transform'])
        rotation = nd('FunctionNodeCombineTransform')
        links.new(parts.outputs['Rotation'], rotation.inputs['Rotation'])
        transposed = nd('FunctionNodeTransposeMatrix')
        links.new(rotation.outputs['Transform'], transposed.inputs['Matrix'])
        basis = nd('FunctionNodeCombineMatrix')
        stated = self.host['world_basis']
        for column in range(4):
            for row in range(4):
                value = float(stated[row][column]) if row < 3 and column < 3 else (1.0 if row == column else 0.0)
                basis.inputs['Column {0} Row {1}'.format(column + 1, row + 1)].default_value = value
        multiplied = nd('FunctionNodeMatrixMultiply')
        links.new(transposed.outputs['Matrix'], multiplied.inputs[0])
        links.new(basis.outputs['Matrix'], multiplied.inputs[1])
        view_rotation = split(multiplied.outputs['Matrix'])

        a = entry(host_projection, 2, 2)
        b = entry(host_projection, 3, 2)
        orthographic = entry(host_projection, 3, 3)
        perspective = math('SUBTRACT', 1.0, orthographic)
        near_switch = nd('GeometryNodeSwitch')
        near_switch.input_type = 'FLOAT'
        links.new(math('GREATER_THAN', orthographic, 0.5), near_switch.inputs['Switch'])
        links.new(math('DIVIDE', b, math('SUBTRACT', a, 1.0)), near_switch.inputs['False'])
        links.new(math('DIVIDE', math('ADD', b, 1.0), a), near_switch.inputs['True'])
        far_switch = nd('GeometryNodeSwitch')
        far_switch.input_type = 'FLOAT'
        links.new(math('GREATER_THAN', orthographic, 0.5), far_switch.inputs['Switch'])
        links.new(math('DIVIDE', b, math('ADD', a, 1.0)), far_switch.inputs['False'])
        links.new(math('DIVIDE', math('SUBTRACT', b, 1.0), a), far_switch.inputs['True'])
        near = near_switch.outputs[0]
        far = far_switch.outputs[0]
        depth_scale = math('SUBTRACT', math('DIVIDE', far, near), 1.0)

        resolution_info = nd('CompositorNodeImageInfo')
        links.new(render.outputs['Image'], resolution_info.inputs['Image'])
        resolution = nd('ShaderNodeSeparateXYZ')
        links.new(resolution_info.outputs['Resolution'], resolution.inputs[0])
        width = resolution.outputs['X']
        height = resolution.outputs['Y']

        unbased = nd('FunctionNodeTransposeMatrix')
        links.new(basis.outputs['Matrix'], unbased.inputs['Matrix'])
        located = nd('FunctionNodeTransformPoint')
        links.new(parts.outputs['Translation'], located.inputs['Vector'])
        links.new(unbased.outputs['Matrix'], located.inputs['Transform'])

        answers = {
            'ssrCameraPosition': (located.outputs['Vector'], None),
            'ssrZBufferParams': (combine(depth_scale, 1.0, math('DIVIDE', depth_scale, far)), math('DIVIDE', 1.0, far)),
            'ssrScreenSize': (combine(width, height, math('DIVIDE', 1.0, width)), math('DIVIDE', 1.0, height)),
            'ssrPerspective': (perspective, None),
        }
        for column in range(4):
            answers['ssrProjection{0}'.format(column)] = (
                combine(entry(projection, column, 0), entry(projection, column, 1), entry(projection, column, 2)),
                entry(projection, column, 3))
            answers['ssrInverseProjection{0}'.format(column)] = (
                combine(entry(inverse_projection, column, 0), entry(inverse_projection, column, 1),
                        entry(inverse_projection, column, 2)),
                entry(inverse_projection, column, 3))
        for row in range(3):
            answers['ssrViewRotation{0}'.format(row)] = (
                combine(entry(view_rotation, 0, row), entry(view_rotation, 1, row), entry(view_rotation, 2, row)), None)
        return answers

    def _bloom_pyramid(self, tree, scene, source, chain, width, height, resolution):
        """泛光金字塔(HDRP DoBloom 的调度,本管线 UberPostPassUtils.PrepareBloomData 的尺寸,GameAssembly 逐指令对过):
        出图高于基准高度时整座金字塔按 基准 / 高 缩;级数 = clamp(FloorToInt(log2(max(截断(宽·缩), 截断(高·缩))) - 1
        - (分辨率档不是半分辨率 ? 1 : 0)), 1, 上限);第 i 级 = RoundToInt(边 · 缩 / 2^(i+1)),至少 1(四舍六入五成双)。
        预滤出第 0 级;每一级先横后纵各模糊一趟(第 0 级在预滤结果上,其余在上一级上按本级纹素中心取 = 2×2 降采样);
        再自底向上,本级模糊结果与下一级(最底一级用它自己的模糊结果,其余用上采样结果)按散射插值。只有一级时出图就是
        预滤结果。每一级的尺寸由喂给该趟的坐标场定(见 _level_domain)。"""
        passes = {entry['group']: entry for entry in (self.post.get('passes') or {}).values()}
        prefilter = passes[chain['roles']['prefilter']]
        blur = passes[chain['roles']['blur']]
        upsample = passes[chain['roles']['upsample']]
        fed = {'sourceTexel', 'texel', 'direction', 'bicubic'}
        if fed != set(chain['geometry']):
            raise RuntimeError('[ruri-post] 泛光金字塔按级给的是 {0},清单声明的是 {1}'.format(sorted(fed), sorted(chain['geometry'])))
        reference = float(chain['height_reference'])
        scale = reference / height if height > reference else 1.0
        largest = max(int(width * scale), int(height * scale))
        levels = int(math.floor(math.log2(largest) - 1.0 - (0.0 if int(round(resolution)) == 2 else 1.0)))
        levels = min(max(levels, 1), int(chain['max_levels']))
        sizes = [(max(1, int(round(width * scale / 2.0 ** (level + 1)))),
                  max(1, int(round(height * scale / 2.0 ** (level + 1))))) for level in range(levels)]

        def run(entry, coordinate, images, geometry):
            node = tree.nodes.new('CompositorNodeGroup')
            node.node_tree = self.group(entry['group'])
            node[self.POST_CHAIN_KEY] = chain['kind']
            tree.links.new(coordinate, node.inputs[entry['coords'][0]])
            if len(images) != len(entry['images']):
                raise RuntimeError('[ruri-post] {0} 要 {1} 张图,链给了 {2} 张'.format(
                    entry['group'], entry['images'], len(images)))
            for name, image in zip(entry['images'], images):
                tree.links.new(image, node.inputs[name])
            for name, value in geometry.items():
                if not self._write_input(node, name, value, entry['sizes'][name]):
                    raise KeyError('[ruri-post] {0} 上没有按级给的口 {1}'.format(entry['group'], name))
            return node.outputs[entry['out']]

        fields = [self._level_domain(tree, scene, chain['kind'], level, size) for level, size in enumerate(sizes)]
        texels = [(1.0 / size[0], 1.0 / size[1]) for size in sizes]
        prefiltered = run(prefilter, fields[0], [source], {'sourceTexel': (1.0 / width, 1.0 / height)})
        if levels == 1:
            return prefiltered
        down = []
        for level in range(levels):
            upper = prefiltered if level == 0 else down[level - 1]
            across = run(blur, fields[level], [upper], {'texel': texels[level], 'direction': (1.0, 0.0)})
            down.append(run(blur, fields[level], [across], {'texel': texels[level], 'direction': (0.0, 1.0)}))
        up = down[levels - 1]
        for level in range(levels - 2, -1, -1):
            lower = sizes[level + 1]
            up = run(upsample, fields[level], [down[level], up],
                     {'texel': texels[level], 'bicubic': (lower[0], lower[1], 1.0 / lower[0], 1.0 / lower[1])})
        return up

    def refresh_chains(self, scene):
        """出图尺寸、分辨率档或哪条链该开变了就重建图链(级数与各级尺寸都随它们);主入口不动,记下的值照原样写回各组。"""
        if not self.post.get('chains') or not self.installed(scene):
            return False
        tree = scene.compositing_node_group
        stage = self.stage_node(scene)
        signature = self._chain_signature(scene, tree)
        if list(tree.get(self.POST_SIZE_KEY) or []) == signature:
            return False
        tree[self.POST_SIZE_KEY] = signature
        for node in [candidate for candidate in tree.nodes if candidate.get(self.POST_CHAIN_KEY) is not None]:
            self._drop_drivers(tree, node)
            tree.nodes.remove(node)
        render = next(node for node in tree.nodes if node.bl_idname == 'CompositorNodeRLayers')
        self._build_chains(scene, tree, render, stage)
        remembered = self._stored(scene, self.POST_VALUES_KEY)
        if remembered:
            self._write_values(tree, remembered)
        _frame_generated(tree)
        return True

    @staticmethod
    @bpy.app.handlers.persistent
    def sync_post_chains(scene, depsgraph):
        """场景合成树被改过 → 把面板上拧过的旋钮记回场景(合成树是插件数据、存盘不带,旋钮只有记在场景上才随文件走),
        各后处理级的图链重判排到下一拍。面板拧的是主入口上的口,Blender 不给回调,只能依赖图落定后看这一拍更新的
        数据块里有没有这棵树;没被改就一行都不多付。

        图链不在这里重建:重建会删掉级域图,而同一轮依赖图更新里排在后面的处理器还在逐条读这一轮的更新,读到的
        是已释放的原件(实测开文件后第一拍当场崩)。交给计时器,落在依赖图更新之外;后台没有事件循环,改图链的
        脚本走 set_extra / set_viewport_preview / refresh_chains,它们自己重判。"""
        tree = scene.compositing_node_group
        if tree is None or not any(isinstance(update.id, bpy.types.CompositorNodeTree) and update.id.original == tree
                                   for update in depsgraph.updates):
            return
        for stack in STACKS:
            if stack.post is not None and stack.installed(scene):
                stack._capture_knobs(scene)
        if not bpy.app.background and not bpy.app.timers.is_registered(Stack._refresh_post_chains):
            bpy.app.timers.register(Stack._refresh_post_chains, first_interval=0.0)

    @staticmethod
    def _refresh_post_chains():
        scene = bpy.context.scene
        if scene is not None:
            for stack in STACKS:
                if stack.post is not None and stack.installed(scene):
                    stack.refresh_chains(scene)
        return None

    @staticmethod
    def _drop_drivers(tree, node):
        """节点删了驱动不会跟着走(驱动住在树的动画数据上,按路径指向节点):先把指向它的那些删掉。"""
        animation = tree.animation_data
        if animation is None:
            return
        prefix = 'nodes["{0}"]'.format(node.name)
        for curve in [curve for curve in animation.drivers if curve.data_path.startswith(prefix)]:
            animation.drivers.remove(curve)

    def install(self, scene):
        self._post_remember(scene)
        group = self.group(self.post['group'])
        stale = bpy.data.node_groups.get(self.post['scene_tree'])
        if stale is not None:
            bpy.data.node_groups.remove(stale)
        tree = _host().plugin_data(bpy.data.node_groups.new(self.post['scene_tree'], 'CompositorNodeTree'))
        tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        render = tree.nodes.new('CompositorNodeRLayers')
        split = tree.nodes.new('CompositorNodeSeparateColor')
        pack = tree.nodes.new('ShaderNodeCombineXYZ')
        pack[self.POST_PACK_KEY] = 1
        stage = tree.nodes.new('CompositorNodeGroup')
        stage.node_tree = group
        unpack = tree.nodes.new('ShaderNodeSeparateXYZ')
        join = tree.nodes.new('CompositorNodeCombineColor')
        output = tree.nodes.new('NodeGroupOutput')
        tree.links.new(render.outputs['Image'], split.inputs['Image'])
        for channel, axis in (('Red', 'X'), ('Green', 'Y'), ('Blue', 'Z')):
            tree.links.new(split.outputs[channel], pack.inputs[axis])
        tree.links.new(pack.outputs['Vector'], stage.inputs[self.post['color_in']])
        tree.links.new(stage.outputs[self.post['color_out']], unpack.inputs['Vector'])
        for channel, axis in (('Red', 'X'), ('Green', 'Y'), ('Blue', 'Z')):
            tree.links.new(unpack.outputs[axis], join.inputs[channel])
        tree.links.new(render.outputs['Alpha'], join.inputs['Alpha'])
        tree.links.new(join.outputs['Image'], output.inputs['Image'])
        if self.post.get('coords'):
            coordinates = tree.nodes.new('CompositorNodeImageCoordinates')
            tree.links.new(render.outputs['Image'], coordinates.inputs['Image'])
            for name in self.post['coords']:
                tree.links.new(coordinates.outputs['Normalized'], stage.inputs[name])
        scene.compositing_node_group = tree
        # 场景记下的输入先写上主入口,再建图链:链开不开读的就是主入口此刻的值。先建后写 = 第一版链按接口缺省判,
        # 下一拍依赖图更新里签名一变又整条重建 —— 那一遍删掉的级域图,同一轮后面的处理器还在读(读已释放的原件,崩)。
        self._apply_stored(scene)
        self._build_chains(scene, tree, render, stage)
        remembered = self._stored(scene, self.POST_VALUES_KEY)
        if remembered:
            self._write_values(tree, remembered)
        _frame_generated(tree)
        scene.render.use_compositing = True
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.shading.use_compositor = 'ALWAYS'
                        # 生成栈的材质全部光照来自原生光循环,一盏灯都没有就一片黑;Material Preview
                        # 出厂关着 Scene Lights(只用工作室 HDRI),所以装后处理链时一并打开,
                        # 否则默认视口里游戏内容黑一片、只有 Rendered 才亮(实测视口 34% 纯黑)。
                        space.shading.use_scene_lights = True
        return tree

    def _apply_stored(self, scene):
        """刚建好的树按场景记下的输入写回:关卡的宿主输入写进各组实例的同名口,面板旋钮写回主入口,图像口按记下的
        纹素现建。记下的某一格在当前产物里已经没有口了 —— 说出来并丢掉:它哪儿都写不进去,留着每次开文件都再喊一遍。"""
        tree = scene.compositing_node_group
        remembered = self._stored(scene, self.POST_VALUES_KEY)
        extra = set(self.extra_inputs())
        orphans = sorted(name for name in remembered if name not in extra)
        if orphans:
            print('[ruri-post] !! 场景记下的关卡输入 {0} 在本级 {1} 里已经没有了,丢掉'.format(
                orphans, self.post['group']), flush=True)
            remembered = {name: value for name, value in remembered.items() if name in extra}
            self._store(scene, self.POST_VALUES_KEY, remembered)
        if remembered:
            self._write_values(tree, remembered)
        stage = self.stage_node(scene)
        knobs = self._stored(scene, self.POST_KNOBS_KEY)
        kept = {}
        for name, components in knobs.items():
            socket = stage.inputs.get(name)
            if socket is None or socket.is_linked or not hasattr(socket, 'default_value'):
                print('[ruri-post] !! 场景记下的旋钮 {0} 在本级 {1} 的主入口上已经没有了,丢掉'.format(
                    name, self.post['group']), flush=True)
                continue
            if socket.type == 'INT':
                socket.default_value = int(round(components[0]))
            elif socket.type == 'BOOLEAN':
                socket.default_value = components[0] > 0.5
            else:
                socket.default_value = components[0] if len(components) == 1 else components
            kept[name] = components
        if kept != knobs:
            self._store(scene, self.POST_KNOBS_KEY, kept)
        self._wire_images(scene)

    def uninstall(self, scene):
        saved = scene.get(self.POST_SAVED_KEY)
        scene_tree = bpy.data.node_groups.get(self.post['scene_tree'])
        if scene.compositing_node_group is scene_tree:
            scene.compositing_node_group = None
        if scene_tree is not None:
            bpy.data.node_groups.remove(scene_tree)
        self._drop_level_domains(scene)
        if saved is None:
            return False
        restored = bpy.data.node_groups.get(saved.get('group') or '')
        if restored is not None:
            scene.compositing_node_group = restored
        scene.render.use_compositing = bool(saved.get('use_compositing', True))
        scene.view_settings.view_transform = saved.get('view_transform', 'Standard')
        scene.view_settings.look = saved.get('look', 'None')
        del scene[self.POST_SAVED_KEY]
        return True

    # ==================== 宿主注册 ====================

    def _host_module(self):
        import importlib
        return importlib.import_module(self.host['registry_module'])

    def register_host(self):
        host = self._host_module()
        stated = self.host.get('world_basis')
        if stated:
            answer = [[float(value) for value in row]
                      for row in getattr(host, self.host.get('world_basis_fn') or 'world_basis')()]
            if any(abs(answer[i][j] - float(stated[i][j])) > 1e-6 for i in range(3) for j in range(3)):
                raise RuntimeError('[Ruri] 宿主世界基与生成期声明的不一致:宿主 {0},清单 {1}。'
                                   '模板组按清单那份物化,照这样挂上去世界量全按错轴算 -- 拒绝加载,'
                                   '改配方的 Host.WorldBasis 后重新生成。'.format(answer, stated))
        if self.post is not None:
            fn = self.host.get('register_post_stage_fn')
            if fn:
                getattr(host, fn)(self)
            if self.engine_global_sources:
                getattr(host, self.host['register_level_globals_fn'])(self.level_global_bases)
            if self.level_images:
                getattr(host, self.host['register_volume_textures_fn'])(self.level_image_layouts)
            if self.sync_post_chains not in bpy.app.handlers.depsgraph_update_post:
                bpy.app.handlers.depsgraph_update_post.append(self.sync_post_chains)
            return
        getattr(host, self.host['register_fn'])(self.provider)
        host.register_material_compile(self.compile_all)
        getattr(host, self.host['register_vertex_stage_fn'])(self.apply_vertex_stage)
        # 顶点腿拆成三段落在三个事实上:建树只认导入,输出像素数只重灌那两格,骨名只重接基座。
        # 合在一条上的代价是推一下镜头就按材质现值重判一次描边 —— 用户删掉的修改器会自己回来。
        host.register_camera_stage(self.push_screen_size)
        host.register_rig_stage(self.apply_rig_basis)
        host.register_capability_rewire(self.rewire_capabilities)
        getattr(host, self.host['register_level_globals_fn'])(self.level_global_bases)
        getattr(host, self.host['register_volume_textures_fn'])(self.level_image_layouts)
        host.register_object_attributes(self.object_attribute_bases)
        host.register_texel_sizes(self.texel_size_bases)
        host.register_light_parameters(self.light_parameter_attributes)
        host.register_render_footprints(self.render_footprint_attributes)
        host.register_view_windows(self.view_window_attributes)
        host.register_view_window_readers(self.view_window_readers)
        host.register_material_panel(self)
        # 基座每帧要跟着骨骼走。帧变化与交互摆姿两条都要挂:前者放动画,后者拖骨头。
        # 写的是**对象自定义属性**(逐对象 UBO),不碰材质树;值没变就不写,所以挂在
        # depsgraph 后面也不会自激。
        for handlers in (bpy.app.handlers.frame_change_post,
                         bpy.app.handlers.depsgraph_update_post):
            if self.push_rig_basis not in handlers:
                handlers.append(self.push_rig_basis)
        # 修改器面板是这批 uniform 的第二个编辑面;Blender 不给它改动回调,只能依赖图落定后比对。
        if self.sync_vertex_knobs not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(self.sync_vertex_knobs)
        # 材质记录是图的第二个写入口(别的插件换槽里的图),同样只能依赖图落定后比对。
        if self.follow_material_records not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(self.follow_material_records)

    def unregister_host(self):
        host = self._host_module()
        if self.post is not None:
            if self.sync_post_chains in bpy.app.handlers.depsgraph_update_post:
                bpy.app.handlers.depsgraph_update_post.remove(self.sync_post_chains)
            fn = self.host.get('unregister_post_stage_fn')
            if fn:
                getattr(host, fn)(self)
            if self.engine_global_sources:
                getattr(host, self.host['unregister_level_globals_fn'])(self.level_global_bases)
            if self.level_images:
                getattr(host, self.host['unregister_volume_textures_fn'])(self.level_image_layouts)
            return
        for handlers in (bpy.app.handlers.frame_change_post,
                         bpy.app.handlers.depsgraph_update_post):
            if self.push_rig_basis in handlers:
                handlers.remove(self.push_rig_basis)
        if self.sync_vertex_knobs in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(self.sync_vertex_knobs)
        if self.follow_material_records in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(self.follow_material_records)
        host.unregister_material_panel(self)
        getattr(host, self.host['unregister_fn'])(self.provider)
        host.unregister_material_compile(self.compile_all)
        getattr(host, self.host['unregister_vertex_stage_fn'])(self.apply_vertex_stage)
        host.unregister_camera_stage(self.push_screen_size)
        host.unregister_rig_stage(self.apply_rig_basis)
        host.unregister_capability_rewire(self.rewire_capabilities)
        getattr(host, self.host['unregister_level_globals_fn'])(self.level_global_bases)
        getattr(host, self.host['unregister_volume_textures_fn'])(self.level_image_layouts)
        host.unregister_object_attributes(self.object_attribute_bases)
        host.unregister_texel_sizes(self.texel_size_bases)
        host.unregister_light_parameters(self.light_parameter_attributes)
        host.unregister_render_footprints(self.render_footprint_attributes)
        host.unregister_view_windows(self.view_window_attributes)
        host.unregister_view_window_readers(self.view_window_readers)


# ==================== 栈发现(同目录 .blend 即产物;清单不符响亮拒绝) ====================

STACKS = []


def _build_stacks():
    """清单**内联在本模块里**,所以建栈是纯 python —— 插件 register() 跑在 Blender 的受限上下文
    (`bpy.data` 此刻是 _RestrictData),那时读不了 .blend 里的任何东西,而宿主又要在注册期
    拿 INTERFACE 建 PropertyGroup。模板 link 因此推迟到第一次真用,见 _linked_group。"""
    folder = os.path.dirname(os.path.abspath(__file__))
    stacks = []
    for manifest in MANIFESTS:
        path = os.path.join(folder, manifest['blend'])
        if not os.path.isfile(path):
            raise RuntimeError('[ruri-uber] 缺配套产物 {0}:全部产物必须同批出货,请重新 codegen'.format(path))
        stacks.append(Stack(folder, manifest))
    return stacks


def purge_plugin_data():
    """宿主 load pass 的第一步(随后按记录重编):清掉本平台各栈留在 Main 里、不带宿主插件记号的插件数据块 ——
    上一版产物存进旧文件的那一份:产物组(带 stamp 键)、模板材质、中性图、顶点腿的树、
    合成树与它的图。认人只用本平台自己的词汇(键名与清单里的名字),不猜。本运行时出生的都带宿主的插件
    记号,由宿主那一步清。进程态随 Main 一起作废。返回清掉几个。"""
    RIG_DRIVEN.clear()
    RIG_SCANNED[0] = False
    vertex_prefixes = tuple(stack.VTX_TREE_PREFIX for stack in STACKS if stack.post is None)
    scene_trees = {stack.post['scene_tree'] for stack in STACKS if stack.post is not None}
    post_prefixes = tuple(stack.post['group'] + ' ' for stack in STACKS if stack.post is not None)
    doomed = [group for group in bpy.data.node_groups if group.library is None and (
        group.get('ruri_stamp') is not None or group.name in scene_trees or group.name.startswith(vertex_prefixes))]
    doomed += [material for material in bpy.data.materials
               if material.library is None and material.get(Stack.TEMPLATE_KEY) is not None]
    doomed += [image for image in bpy.data.images if image.library is None and (
        image.get('ruri_placeholder') or image.name.startswith(post_prefixes))]
    if doomed:
        bpy.data.batch_remove(doomed)
    return len(doomed)


def register():
    STACKS.clear()
    STACKS.extend(_build_stacks())
    hosts = {manifest['host']['registry_module'] for manifest in MANIFESTS}
    if len(hosts) != 1:
        raise RuntimeError('[ruri-uber] 本平台各栈清单声明的宿主不止一个:{0}'.format(sorted(hosts)))
    _host().register_plugin_purge(purge_plugin_data)
    for stack in STACKS:
        stack.register_host()


def unregister():
    try:
        _host().unregister_plugin_purge(purge_plugin_data)
    except Exception as exc:
        print('[ruri-uber] 注销失败: {0}'.format(exc), flush=True)
    for stack in reversed(STACKS):
        try:
            stack.unregister_host()
        except Exception as exc:
            print('[ruri-uber] 注销失败: {0}'.format(exc), flush=True)
    STACKS.clear()